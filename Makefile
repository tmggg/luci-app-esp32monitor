include $(TOPDIR)/rules.mk

PKG_VERSION:=1.0.0
PKG_RELEASE:=9
LUCI_TITLE:=LuCI ESP32 status monitor
LUCI_DEPENDS:=+luci-base +rpcd-mod-file +uhttpd +uci +ubus +jsonfilter +coreutils-sha256sum +coreutils-stat
LUCI_PKGARCH:=all

define Package/luci-app-esp32monitor/conffiles
/etc/config/esp32monitor
endef

include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt buildroot signature
