// CSK sensor firmware
// -------------------
// A receive-only sensor. The ESP32 never transmits WiFi frames or BLE
// advertisements/scan requests; it only listens. That means it can't be
// noticed by the things it's looking for.
//
// What it does:
//   * WiFi: promiscuous mode, hopping 2.4 GHz channels 1-13. Parses beacons,
//     probe requests, deauth/disassoc and data-frame headers (to find client
//     devices and measure how much they upload, e.g. a camera streaming video).
//   * BLE: passive scan, sends the raw advertisement payload to the host, which
//     does all the decoding (AirTag / Tile / SmartTag / Google FMDN / ...).
//
// Output: one JSON object per line on USB serial at 921600 baud. WiFi data is
// summed per 1.5 s window so the serial link isn't flooded with beacons.
//
// Input (newline-terminated commands from host):
//   ch <n>    lock to channel n (1-13), "ch 0" resumes hopping
//   ble <0|1> disable/enable BLE scanning
//   wifi <0|1> disable/enable WiFi sniffing
//   ping      -> replies {"t":"pong"}
//   karma <ch> <ssid-hex>
//             ACTIVE TEST (the only thing that ever transmits): sends 3 probe
//             requests for a made-up SSID from a random MAC on channel <ch> and
//             reports every BSSID that claims to be it -> {"t":"karma",...}.
//             Only a Karma/Pineapple-style rogue AP answers for a network that
//             doesn't exist.

#include <Arduino.h>
#include <WiFi.h>
#include "esp_wifi.h"
#ifndef CSK_NO_BLE
#include <NimBLEDevice.h>
#endif

#define FW_VERSION "csk-0.1.0"
#define SERIAL_BAUD 921600

static const uint32_t FLUSH_MS = 1500;
static const uint32_t HOP_MS = 200;
static const uint32_t BLE_MIN_REPORT_MS = 600;  // per-address rate limit

// Channel hop order: spends extra time on 1/6/11 where most APs live.
static const uint8_t HOP_SEQ[] = {1, 6, 11, 2, 7, 12, 1, 6, 11, 3, 8, 13, 1, 6, 11, 4, 9, 5, 10};

// ---------------------------------------------------------------- events ---
enum EvType : uint8_t { EV_BEACON, EV_PROBE, EV_DEAUTH, EV_DATA, EV_BLE, EV_KARMA };

struct Event {
  uint8_t type;
  int8_t rssi;
  uint8_t ch;
  uint8_t flags;    // beacon: enc (0 open,1 wep,2 wpa); deauth: 1=disassoc; data: 1=uplink; ble: addr type
  uint8_t a[6];     // beacon: bssid | probe: src | deauth: src | data: station | ble: address
  uint8_t b[6];     // deauth: dst | data: bssid
  uint16_t len;     // data: frame length | deauth: reason code
  uint8_t dlen;     // valid bytes in data[]
  uint8_t data[31]; // ssid (<=32 truncated to 31) or BLE adv payload (<=31)
  uint32_t fp;      // beacon: fingerprint of the information-element layout
};

static QueueHandle_t evq;
static volatile uint32_t dropped = 0;
static volatile uint8_t curCh = 1;
static uint8_t lockCh = 0;
static bool wifiOn = true;
static bool bleOn = true;

// Karma canary test state
static volatile bool karmaActive = false;
static uint8_t karmaSsid[32];
static uint8_t karmaLen = 0;
static uint8_t karmaSrc[6];
static uint8_t karmaCh = 0, karmaPrevLock = 0, karmaSent = 0;
static uint32_t karmaStart = 0;
static uint8_t karmaHits[8][6];
static uint8_t nKarmaHits = 0;
static const uint32_t KARMA_WINDOW_MS = 2500;

static inline void pushEvent(const Event& e) {
  if (xQueueSend(evq, &e, 0) != pdTRUE) dropped++;
}

// ------------------------------------------------------------- WiFi sniff ---
// FNV-1a over the beacon's element layout. Different AP firmwares (Comcast
// gateway vs hostapd on a Pineapple) produce different layouts, while real APs
// of one network/model share one. Only stable parts are hashed: element IDs,
// plus contents of rates (1, 50), RSN (48) and vendor OUI+type (221).
static inline void fnv(uint32_t& h, const uint8_t* d, int n) {
  for (int i = 0; i < n; i++) { h ^= d[i]; h *= 16777619u; }
}

static void parseTags(const uint8_t* p, int len, Event& e, bool* rsn, bool* wpa, uint32_t* fp = nullptr) {
  int i = 0;
  uint32_t h = 2166136261u;
  while (i + 2 <= len) {
    uint8_t id = p[i], l = p[i + 1];
    if (i + 2 + l > len) break;
    const uint8_t* v = p + i + 2;
    if (fp && id != 0 && id != 3 && id != 5 && id != 11 && id != 42) {
      fnv(h, &id, 1);
      if (id == 1 || id == 50 || id == 48) fnv(h, v, l);
      else if (id == 221 && l >= 4) fnv(h, v, 4);
    }
    if (id == 0) {
      e.dlen = l > sizeof(e.data) ? sizeof(e.data) : l;
      memcpy(e.data, v, e.dlen);
    } else if (id == 3 && l >= 1) {
      e.ch = v[0];
    } else if (id == 48 && rsn) {
      *rsn = true;
    } else if (id == 221 && wpa && l >= 4 && v[0] == 0x00 && v[1] == 0x50 && v[2] == 0xF2 && v[3] == 0x01) {
      *wpa = true;
    }
    i += 2 + l;
  }
  if (fp) *fp = h;
}

static void IRAM_ATTR snifferCb(void* buf, wifi_promiscuous_pkt_type_t type) {
  const wifi_promiscuous_pkt_t* pkt = (const wifi_promiscuous_pkt_t*)buf;
  int len = pkt->rx_ctrl.sig_len;
  if (type == WIFI_PKT_MGMT) len -= 4;  // strip FCS
  if (len < 24) return;
  const uint8_t* f = pkt->payload;
  uint8_t ftype = (f[0] >> 2) & 3, sub = f[0] >> 4, fl = f[1];

  Event e;
  memset(&e, 0, sizeof(e));
  e.rssi = pkt->rx_ctrl.rssi;
  e.ch = pkt->rx_ctrl.channel;

  if (type == WIFI_PKT_MGMT && ftype == 0) {
    if (sub == 8 || sub == 5) {  // beacon / probe response
      if (len < 36) return;
      e.type = EV_BEACON;
      memcpy(e.a, f + 16, 6);
      bool rsn = false, wpa = false;
      parseTags(f + 36, len - 36, e, &rsn, &wpa, sub == 8 ? &e.fp : nullptr);
      uint16_t cap = f[34] | (f[35] << 8);
      e.flags = (rsn || wpa) ? 2 : ((cap & 0x0010) ? 1 : 0);
      pushEvent(e);
      if (sub == 5 && karmaActive && e.dlen == karmaLen && !memcmp(e.data, karmaSsid, karmaLen)) {
        e.type = EV_KARMA;  // someone answered for a network that doesn't exist
        pushEvent(e);
      }
    } else if (sub == 4) {  // probe request
      e.type = EV_PROBE;
      memcpy(e.a, f + 10, 6);
      parseTags(f + 24, len - 24, e, nullptr, nullptr);
      pushEvent(e);
    } else if (sub == 12 || sub == 10) {  // deauth / disassoc
      e.type = EV_DEAUTH;
      e.flags = (sub == 10);
      memcpy(e.a, f + 10, 6);
      memcpy(e.b, f + 4, 6);
      e.len = len >= 26 ? (f[24] | (f[25] << 8)) : 0;
      pushEvent(e);
    }
  } else if (type == WIFI_PKT_DATA && ftype == 2) {
    bool toDS = fl & 0x01, fromDS = fl & 0x02;
    if (toDS == fromDS) return;  // ignore ad-hoc / WDS
    e.type = EV_DATA;
    if (toDS) {  // station -> AP
      memcpy(e.b, f + 4, 6);
      memcpy(e.a, f + 10, 6);
      e.flags = 1;
    } else {     // AP -> station
      memcpy(e.a, f + 4, 6);
      memcpy(e.b, f + 10, 6);
    }
    if (e.a[0] & 0x01) return;  // multicast/broadcast
    e.len = pkt->rx_ctrl.sig_len;
    pushEvent(e);
  }
}

static void wifiStart() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_ps(WIFI_PS_NONE);
  wifi_promiscuous_filter_t filt = {.filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT | WIFI_PROMIS_FILTER_MASK_DATA};
  esp_wifi_set_promiscuous_filter(&filt);
  esp_wifi_set_promiscuous_rx_cb(&snifferCb);
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(curCh, WIFI_SECOND_CHAN_NONE);
}

// --------------------------------------------------------------- BLE scan ---
#ifndef CSK_NO_BLE
struct BleSeen { uint8_t a[6]; uint32_t t; };
static BleSeen bleSeen[96];

class AdvCb : public NimBLEAdvertisedDeviceCallbacks {
  void onResult(NimBLEAdvertisedDevice* d) override {
    Event e;
    memset(&e, 0, sizeof(e));
    unsigned int m[6];
    std::string s = d->getAddress().toString();
    if (sscanf(s.c_str(), "%x:%x:%x:%x:%x:%x", &m[0], &m[1], &m[2], &m[3], &m[4], &m[5]) != 6) return;
    for (int i = 0; i < 6; i++) e.a[i] = m[i];

    // Rate limit per address (simple direct-mapped cache).
    uint32_t now = millis();
    uint8_t h = (e.a[3] ^ e.a[4] * 7 ^ e.a[5] * 31) % (sizeof(bleSeen) / sizeof(bleSeen[0]));
    if (memcmp(bleSeen[h].a, e.a, 6) == 0 && now - bleSeen[h].t < BLE_MIN_REPORT_MS) return;
    memcpy(bleSeen[h].a, e.a, 6);
    bleSeen[h].t = now;

    e.type = EV_BLE;
    e.rssi = d->getRSSI();
    e.flags = d->getAddressType();
    size_t n = d->getPayloadLength();
    if (n > sizeof(e.data)) n = sizeof(e.data);
    e.dlen = n;
    memcpy(e.data, d->getPayload(), n);
    pushEvent(e);
  }
};
static AdvCb advCb;

static void bleStart() {
  NimBLEDevice::init("");
  NimBLEScan* sc = NimBLEDevice::getScan();
  sc->setAdvertisedDeviceCallbacks(&advCb, true);  // want duplicates (RSSI updates)
  sc->setActiveScan(false);                        // passive: never send scan requests
  sc->setInterval(100);
  sc->setWindow(60);                               // leave airtime for WiFi coexistence
  sc->setMaxResults(0);                            // callbacks only, don't store
  sc->start(0, nullptr, false);
}
static void bleStop() { NimBLEDevice::getScan()->stop(); }
static void bleResume() { NimBLEDevice::getScan()->start(0, nullptr, false); }
#endif

// ------------------------------------------------------------ aggregation ---
struct ApRow { uint8_t b[6]; uint8_t s[31]; uint8_t sl; uint8_t ch, enc; int8_t r; uint16_t n; uint32_t fp; };
struct StaRow { uint8_t m[6], b[6]; uint8_t ch; int8_t r; uint16_t uf; uint32_t ub, db; };
struct ProbeRow { uint8_t m[6]; uint8_t s[31]; uint8_t sl; uint8_t ch; int8_t r; uint16_t n; };
struct DeauthRow { uint8_t s[6], d[6]; uint8_t ch, dis; int8_t r; uint16_t rs, n; };

static ApRow aps[128];       static int nAp;
static StaRow stas[128];     static int nSta;
static ProbeRow probes[64];  static int nProbe;
static DeauthRow deauths[32]; static int nDeauth;
static uint32_t overflow = 0;

static char line[256];

static int fmtMac(char* o, const uint8_t* m) {
  return sprintf(o, "%02x:%02x:%02x:%02x:%02x:%02x", m[0], m[1], m[2], m[3], m[4], m[5]);
}
static int fmtHex(char* o, const uint8_t* d, int n) {
  static const char* H = "0123456789abcdef";
  for (int i = 0; i < n; i++) { o[2 * i] = H[d[i] >> 4]; o[2 * i + 1] = H[d[i] & 15]; }
  o[2 * n] = 0;
  return 2 * n;
}
static void emit(int n) { line[n++] = '\n'; Serial.write((const uint8_t*)line, n); }

static void aggregate(const Event& e) {
  switch (e.type) {
    case EV_BEACON: {
      for (int i = 0; i < nAp; i++) {
        ApRow& r = aps[i];
        if (!memcmp(r.b, e.a, 6) && r.sl == e.dlen && !memcmp(r.s, e.data, e.dlen)) {
          r.n++; if (e.rssi > r.r) r.r = e.rssi; if (e.fp) r.fp = e.fp; return;
        }
      }
      if (nAp >= 128) { overflow++; return; }
      ApRow& r = aps[nAp++];
      memcpy(r.b, e.a, 6); memcpy(r.s, e.data, e.dlen); r.sl = e.dlen;
      r.ch = e.ch; r.enc = e.flags; r.r = e.rssi; r.n = 1; r.fp = e.fp;
      return;
    }
    case EV_PROBE: {
      for (int i = 0; i < nProbe; i++) {
        ProbeRow& r = probes[i];
        if (!memcmp(r.m, e.a, 6) && r.sl == e.dlen && !memcmp(r.s, e.data, e.dlen)) {
          r.n++; if (e.rssi > r.r) r.r = e.rssi; return;
        }
      }
      if (nProbe >= 64) { overflow++; return; }
      ProbeRow& r = probes[nProbe++];
      memcpy(r.m, e.a, 6); memcpy(r.s, e.data, e.dlen); r.sl = e.dlen;
      r.ch = e.ch; r.r = e.rssi; r.n = 1;
      return;
    }
    case EV_DEAUTH: {
      for (int i = 0; i < nDeauth; i++) {
        DeauthRow& r = deauths[i];
        if (!memcmp(r.s, e.a, 6) && !memcmp(r.d, e.b, 6)) { r.n++; if (e.rssi > r.r) r.r = e.rssi; return; }
      }
      if (nDeauth >= 32) { overflow++; return; }
      DeauthRow& r = deauths[nDeauth++];
      memcpy(r.s, e.a, 6); memcpy(r.d, e.b, 6);
      r.ch = e.ch; r.dis = e.flags; r.r = e.rssi; r.rs = e.len; r.n = 1;
      return;
    }
    case EV_DATA: {
      StaRow* r = nullptr;
      for (int i = 0; i < nSta; i++) if (!memcmp(stas[i].m, e.a, 6)) { r = &stas[i]; break; }
      if (!r) {
        if (nSta >= 128) { overflow++; return; }
        r = &stas[nSta++];
        memset(r, 0, sizeof(*r));
        memcpy(r->m, e.a, 6); memcpy(r->b, e.b, 6);
        r->ch = e.ch; r->r = -127;
      }
      if (e.flags) { r->uf++; r->ub += e.len; if (e.rssi > r->r) r->r = e.rssi; }
      else r->db += e.len;
      return;
    }
    case EV_KARMA: {
      for (int i = 0; i < nKarmaHits; i++) if (!memcmp(karmaHits[i], e.a, 6)) return;
      if (nKarmaHits < 8) memcpy(karmaHits[nKarmaHits++], e.a, 6);
      return;
    }
    case EV_BLE: {
      int n = sprintf(line, "{\"t\":\"ble\",\"a\":\"");
      n += fmtMac(line + n, e.a);
      n += sprintf(line + n, "\",\"at\":%u,\"r\":%d,\"p\":\"", e.flags, e.rssi);
      n += fmtHex(line + n, e.data, e.dlen);
      n += sprintf(line + n, "\"}");
      emit(n);
      return;
    }
  }
}

static void flush() {
  int n;
  for (int i = 0; i < nAp; i++) {
    ApRow& r = aps[i];
    n = sprintf(line, "{\"t\":\"ap\",\"b\":\"");
    n += fmtMac(line + n, r.b);
    n += sprintf(line + n, "\",\"s\":\"");
    n += fmtHex(line + n, r.s, r.sl);
    n += sprintf(line + n, "\",\"ch\":%u,\"e\":%u,\"r\":%d,\"n\":%u,\"fp\":\"%08lx\"}",
                 r.ch, r.enc, r.r, r.n, (unsigned long)r.fp);
    emit(n);
  }
  for (int i = 0; i < nSta; i++) {
    StaRow& r = stas[i];
    n = sprintf(line, "{\"t\":\"sta\",\"m\":\"");
    n += fmtMac(line + n, r.m);
    n += sprintf(line + n, "\",\"b\":\"");
    n += fmtMac(line + n, r.b);
    n += sprintf(line + n, "\",\"ch\":%u,\"r\":%d,\"uf\":%u,\"ub\":%lu,\"db\":%lu}",
                 r.ch, r.r, r.uf, (unsigned long)r.ub, (unsigned long)r.db);
    emit(n);
  }
  for (int i = 0; i < nProbe; i++) {
    ProbeRow& r = probes[i];
    n = sprintf(line, "{\"t\":\"probe\",\"m\":\"");
    n += fmtMac(line + n, r.m);
    n += sprintf(line + n, "\",\"s\":\"");
    n += fmtHex(line + n, r.s, r.sl);
    n += sprintf(line + n, "\",\"ch\":%u,\"r\":%d,\"n\":%u}", r.ch, r.r, r.n);
    emit(n);
  }
  for (int i = 0; i < nDeauth; i++) {
    DeauthRow& r = deauths[i];
    n = sprintf(line, "{\"t\":\"deauth\",\"src\":\"");
    n += fmtMac(line + n, r.s);
    n += sprintf(line + n, "\",\"dst\":\"");
    n += fmtMac(line + n, r.d);
    n += sprintf(line + n, "\",\"k\":\"%s\",\"rs\":%u,\"ch\":%u,\"r\":%d,\"n\":%u}",
                 r.dis ? "disassoc" : "deauth", r.rs, r.ch, r.r, r.n);
    emit(n);
  }
#ifdef CSK_LED_PIN
  // Heartbeat: brief blink each flush so you can see the sensor is alive.
  digitalWrite(CSK_LED_PIN, HIGH);
  delay(15);
  digitalWrite(CSK_LED_PIN, LOW);
#endif
  n = sprintf(line, "{\"t\":\"stat\",\"up\":%lu,\"ch\":%u,\"lock\":%u,\"wifi\":%u,\"ble\":%u,\"drop\":%lu,\"ovf\":%lu,\"heap\":%lu}",
              (unsigned long)millis(), curCh, lockCh, wifiOn, bleOn,
              (unsigned long)dropped, (unsigned long)overflow, (unsigned long)ESP.getFreeHeap());
  emit(n);
  nAp = nSta = nProbe = nDeauth = 0;
}

// ------------------------------------------------------------ karma test ---
static void sendCanaryProbe() {
  uint8_t f[24 + 2 + 32 + 16];
  int n = 0;
  f[n++] = 0x40; f[n++] = 0x00;           // probe request
  f[n++] = 0x00; f[n++] = 0x00;           // duration
  memset(f + n, 0xff, 6); n += 6;         // DA broadcast
  memcpy(f + n, karmaSrc, 6); n += 6;     // SA random
  memset(f + n, 0xff, 6); n += 6;         // BSSID wildcard
  f[n++] = 0x00; f[n++] = 0x00;           // seq (filled by driver)
  f[n++] = 0x00; f[n++] = karmaLen;       // SSID
  memcpy(f + n, karmaSsid, karmaLen); n += karmaLen;
  static const uint8_t rates[] = {0x01, 0x08, 0x82, 0x84, 0x8b, 0x96, 0x0c, 0x12, 0x18, 0x24,
                                  0x32, 0x04, 0x30, 0x48, 0x60, 0x6c};
  memcpy(f + n, rates, sizeof(rates)); n += sizeof(rates);
  esp_wifi_80211_tx(WIFI_IF_STA, f, n, true);
}

static void startKarma(int ch, const char* hex) {
  int l = strlen(hex) / 2;
  if (ch < 1 || ch > 13 || l < 1 || l > 24 || karmaActive) return;
  for (int i = 0; i < l; i++) { unsigned v; sscanf(hex + 2 * i, "%2x", &v); karmaSsid[i] = v; }
  karmaLen = l;
  uint32_t r1 = esp_random(), r2 = esp_random();
  memcpy(karmaSrc, &r1, 4); memcpy(karmaSrc + 4, &r2, 2);
  karmaSrc[0] = (karmaSrc[0] & 0xFC) | 0x02;  // locally administered, unicast: looks like a phone
  karmaPrevLock = lockCh;
  karmaCh = lockCh = ch;
  curCh = ch;
  esp_wifi_set_channel(curCh, WIFI_SECOND_CHAN_NONE);
  nKarmaHits = 0;
  karmaSent = 0;
  karmaStart = millis();
  karmaActive = true;
}

static void pollKarma(uint32_t now) {
  if (!karmaActive) return;
  uint32_t t = now - karmaStart;
  if (karmaSent < 3 && t >= karmaSent * 250u) { sendCanaryProbe(); karmaSent++; }
  if (t < KARMA_WINDOW_MS) return;
  karmaActive = false;
  int n = sprintf(line, "{\"t\":\"karma\",\"ch\":%u,\"s\":\"", karmaCh);
  n += fmtHex(line + n, karmaSsid, karmaLen);
  n += sprintf(line + n, "\",\"hits\":[");
  for (int i = 0; i < nKarmaHits; i++) {
    n += sprintf(line + n, "%s\"", i ? "," : "");
    n += fmtMac(line + n, karmaHits[i]);
    line[n++] = '"';
  }
  n += sprintf(line + n, "]}");
  emit(n);
  lockCh = karmaPrevLock;
  if (lockCh) { curCh = lockCh; esp_wifi_set_channel(curCh, WIFI_SECOND_CHAN_NONE); }
}

// --------------------------------------------------------------- commands ---
static char cmd[96];
static uint8_t cmdLen = 0;

static void handleCmd(const char* c) {
  int v;
  char hex[64];
  if (sscanf(c, "karma %d %63s", &v, hex) == 2) {
    startKarma(v, hex);
  } else if (sscanf(c, "ch %d", &v) == 1 && v >= 0 && v <= 13) {
    lockCh = v;
    if (v) { curCh = v; esp_wifi_set_channel(curCh, WIFI_SECOND_CHAN_NONE); }
  } else if (sscanf(c, "wifi %d", &v) == 1) {
    wifiOn = v;
    esp_wifi_set_promiscuous(wifiOn);
#ifndef CSK_NO_BLE
  } else if (sscanf(c, "ble %d", &v) == 1) {
    if (v && !bleOn) bleResume();
    if (!v && bleOn) bleStop();
    bleOn = v;
#endif
  } else if (!strcmp(c, "ping")) {
    Serial.println("{\"t\":\"pong\"}");
  }
}

static void pollSerial() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (cmdLen) { cmd[cmdLen] = 0; handleCmd(cmd); cmdLen = 0; }
    } else if (cmdLen < sizeof(cmd) - 1) {
      cmd[cmdLen++] = ch;
    }
  }
}

// ------------------------------------------------------------------- main ---
void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(200);
#ifdef CSK_LED_PIN
  pinMode(CSK_LED_PIN, OUTPUT);
#endif
  evq =xQueueCreate(128, sizeof(Event));
  wifiStart();
#ifndef CSK_NO_BLE
  bleStart();
#else
  bleOn = false;
#endif
  uint8_t mac[6];
  esp_wifi_get_mac(WIFI_IF_STA, mac);
  int n = sprintf(line, "{\"t\":\"hello\",\"fw\":\"%s\",\"chip\":\"%s\",\"mac\":\"", FW_VERSION, ESP.getChipModel());
  n += fmtMac(line + n, mac);
  n += sprintf(line + n, "\"}");
  emit(n);
}

void loop() {
  static uint32_t lastHop = 0, lastFlush = 0;
  static uint8_t hopIdx = 0;
  uint32_t now = millis();

  pollSerial();
  pollKarma(now);

  Event e;
  int budget = 64;
  while (budget-- && xQueueReceive(evq, &e, 0) == pdTRUE) aggregate(e);

  if (wifiOn && !lockCh && now - lastHop >= HOP_MS) {
    hopIdx = (hopIdx + 1) % sizeof(HOP_SEQ);
    curCh = HOP_SEQ[hopIdx];
    esp_wifi_set_channel(curCh, WIFI_SECOND_CHAN_NONE);
    lastHop = now;
  }
  if (now - lastFlush >= FLUSH_MS) {
    flush();
    lastFlush = now;
  }
  if (uxQueueMessagesWaiting(evq) == 0) delay(2);
}
