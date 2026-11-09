# Flash the ESP32 from a Windows PC (no coding, ~5 minutes)

This loads the Demon King firmware onto your ESP32 DevKit V1 using a web page.
You do this once. After it's flashed, the ESP32 goes to the handheld.

## What you need
- Your ESP32 DevKit V1 and a **micro-USB *data* cable** (not charge-only)
- A Windows PC with **Google Chrome or Microsoft Edge** (the flasher uses these;
  it will NOT work in Firefox)

## Step 1 - Install the USB driver (one time)
Windows needs a small driver to see the board as a COM port. The DevKit V1 uses
a "CP2102" chip.
1. Go to Silicon Labs' CP210x driver page:
   https://www.silabs.com/developer-tools/usb-to-uart-bridge-vcp-drivers
2. Download **"CP210x Windows Drivers"**, unzip it, and run the installer
   (`CP210xVCPInstaller_x64.exe`).
3. Restart the PC if it asks.

(If you plug the board in and Windows makes a "device connected" sound and it
shows up as "Silicon Labs CP210x ... (COM3)" in Device Manager, you're set.)

## Step 2 - Download the firmware
Download this one file to your PC (click **Download raw file**):

  demon-king-firmware-devkitv1.bin

(It's in this same folder on GitHub. Just remember where it saved - probably
your Downloads folder.)

## Step 3 - Flash it
1. Plug the ESP32 into the PC with the micro-USB cable.
2. In Chrome or Edge, open:  **https://esp.huhn.me**
3. Click **CONNECT**. A little window lists serial ports - pick the one that
   says **CP210x / COM3** (the number may differ) and click Connect.
4. Next to the **offset box** type `0x0` (zero-ex-zero), click **…/Select**,
   and choose the `demon-king-firmware-devkitv1.bin` you downloaded.
5. Click **PROGRAM**.
6. Watch the log. If it stalls at "Connecting", **hold the little BOOT button
   on the ESP32** for a few seconds until it starts, then let go.
7. When it finishes ("done" / 100%), the firmware is loaded. The blue LED on
   the board blinks about once a second.

That's it - unplug it from the PC. The ESP32 is ready; connect it to the
handheld next.

## If it won't connect
- The board doesn't show up as a COM port → the driver (Step 1) didn't install,
  or the cable is charge-only. Try another cable.
- "Connecting..." never finishes → hold the **BOOT** button while you click
  PROGRAM.
- Still stuck → note the exact message and send it to Claude.

## For the technically inclined
The `.bin` is a merged image (bootloader + partitions + app) meant for offset
`0x0`. You can also flash it with esptool:
`esptool.py --chip esp32 write_flash 0x0 demon-king-firmware-devkitv1.bin`
SHA256 is in `demon-king-firmware-devkitv1.bin.sha256`.
