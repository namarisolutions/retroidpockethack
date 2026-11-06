.PHONY: help test firmware flash package clean

help:
	@echo "make test      - run the host unit tests"
	@echo "make firmware  - build ESP32 firmware (DevKit V1)"
	@echo "make flash      - flash a connected ESP32 (prebuilt or built)"
	@echo "make package   - build dist/csk-demon-king-<version>.tar.gz"
	@echo "make clean     - remove build artifacts"

test:
	cd host && python3 -m pytest -q

firmware:
	cd firmware && pio run

flash:
	cd host && ./flash.sh

package:
	packaging/build-release.sh

clean:
	rm -rf dist firmware/.pio host/.venv host/.flashvenv
	find host -name __pycache__ -type d -prune -exec rm -rf {} +
