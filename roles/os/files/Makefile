SHELL := /bin/bash
.DEFAULT_GOAL := help

BUILD_SCRIPT := ./build.sh
OUT_DIR ?= $(CURDIR)/out
PROV_KEY ?= $(HOME)/.ssh/id_ed25519.pub

.PHONY: help \
	build-arch build-arch-iso build-arch-netboot \
	build-arch-releng-iso build-arch-releng-netboot \
	build-arch-baseline-iso build-arch-baseline-netboot \
	build-debian-trixie build-debian-trixie-iso build-debian-trixie-netboot \
	clean

help:
	@echo "Usage:"
	@echo "  make build-arch-releng-iso PROV_KEY=\$$HOME/.ssh/id_ed25519.pub"
	@echo "  make build-arch-baseline-netboot PROV_KEY=\$$HOME/.ssh/id_ed25519.pub"
	@echo "  make build-debian-trixie-iso PROV_KEY=\$$HOME/.ssh/id_ed25519.pub"
	@echo "  make build-debian-trixie-netboot PROV_KEY=\$$HOME/.ssh/id_ed25519.pub"
	@echo "  make clean"
	@echo
	@echo "Variables:"
	@echo "  PROV_KEY   Public SSH key copied into the image"
	@echo "  OUT_DIR    Base output directory (default: $(OUT_DIR))"
	@echo "  ARCH_PROFILE releng|baseline for build-arch (default: releng)"
	@echo "  ARCH_TYPE    iso|netboot for build-arch (default: iso)"
	@echo "  DEBIAN_TYPE  iso|netboot for build-debian-trixie (default: iso)"

check-provisioning-key:
	@test -f "$(PROV_KEY)" || { echo "Missing PROV_KEY: $(PROV_KEY)" >&2; exit 1; }

build-arch: ARCH_PROFILE ?= releng
build-arch: ARCH_TYPE ?= iso
build-arch: check-provisioning-key
	$(BUILD_SCRIPT) -d arch -o "$(OUT_DIR)/arch_$(ARCH_PROFILE)_$(ARCH_TYPE)" -p "$(PROV_KEY)" -- -p "$(ARCH_PROFILE)" -t "$(ARCH_TYPE)"

build-arch-iso: ARCH_PROFILE ?= releng
build-arch-iso:
	$(MAKE) build-arch ARCH_PROFILE="$(ARCH_PROFILE)" ARCH_TYPE=iso PROV_KEY="$(PROV_KEY)" OUT_DIR="$(OUT_DIR)"

build-arch-netboot: ARCH_PROFILE ?= releng
build-arch-netboot:
	$(MAKE) build-arch ARCH_PROFILE="$(ARCH_PROFILE)" ARCH_TYPE=netboot PROV_KEY="$(PROV_KEY)" OUT_DIR="$(OUT_DIR)"

build-arch-releng-iso:
	$(MAKE) build-arch ARCH_PROFILE=releng ARCH_TYPE=iso PROV_KEY="$(PROV_KEY)" OUT_DIR="$(OUT_DIR)"

build-arch-releng-netboot:
	$(MAKE) build-arch ARCH_PROFILE=releng ARCH_TYPE=netboot PROV_KEY="$(PROV_KEY)" OUT_DIR="$(OUT_DIR)"

build-arch-baseline-iso:
	$(MAKE) build-arch ARCH_PROFILE=baseline ARCH_TYPE=iso PROV_KEY="$(PROV_KEY)" OUT_DIR="$(OUT_DIR)"

build-arch-baseline-netboot:
	$(MAKE) build-arch ARCH_PROFILE=baseline ARCH_TYPE=netboot PROV_KEY="$(PROV_KEY)" OUT_DIR="$(OUT_DIR)"

build-debian-trixie: DEBIAN_TYPE ?= iso
build-debian-trixie: check-provisioning-key
	$(BUILD_SCRIPT) -d debian_trixie -o "$(OUT_DIR)/debian_trixie_$(DEBIAN_TYPE)" -p "$(PROV_KEY)" -- -t "$(DEBIAN_TYPE)"

build-debian-trixie-iso:
	$(MAKE) build-debian-trixie DEBIAN_TYPE=iso PROV_KEY="$(PROV_KEY)" OUT_DIR="$(OUT_DIR)"

build-debian-trixie-netboot:
	$(MAKE) build-debian-trixie DEBIAN_TYPE=netboot PROV_KEY="$(PROV_KEY)" OUT_DIR="$(OUT_DIR)"

clean:
	rm -rf "$(OUT_DIR)"
	rm -f provisioning_key.pub
	-docker image rm -f arch-builder debian-builder
