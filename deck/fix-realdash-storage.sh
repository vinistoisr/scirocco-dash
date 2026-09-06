#!/system/bin/sh
# fix-realdash-storage.sh -- restore RealDash's ability to load its CAN
# description file on this head unit.
#
# THE BUG (diagnosed 2026-08-23 from logcat on the DUDU7):
#   RealDash v2.6.8 targets SDK 37, so scoped storage is fully enforced, but
#   its native NUTS engine still opens description files by RAW PATH. The
#   picker it launches is ACTION_GET_CONTENT, whose URI grants are not
#   persistable by design, so this fires:
#
#     D/NUTS: Exception in uri permission: No persistable permission grants
#             found for UID 10153 and Uri content://com.android.external...
#     E/MediaProvider: Permission to access file: .../scirocco_realdash.xml
#             is denied
#     W/NUTS: File::Open - failed ... reason: Permission denied
#
#   The visible symptom is RealDash's description-file list coming up EMPTY
#   and the import appearing to do nothing. Nothing is wrong with the XML --
#   a 551-byte single-frame file failed identically.
#
#   RealDash does NOT declare MANAGE_EXTERNAL_STORAGE in its manifest, so
#   "All files access" can never be granted to it -- `appops set
#   MANAGE_EXTERNAL_STORAGE allow` silently reverts to `default`. The op that
#   DOES work is NO_ISOLATED_STORAGE, which tells MediaProvider to stop
#   mediating this uid's raw path opens.
#
# Re-run this after: reinstalling RealDash, clearing its data, a factory
# reset, or any Play Store update that resets its appops.
#
#   adb push deck/fix-realdash-storage.sh /data/local/tmp/ && \
#     adb shell sh /data/local/tmp/fix-realdash-storage.sh

PKG=com.napko.RealDash

cmd appops set --uid $PKG NO_ISOLATED_STORAGE allow
cmd appops set       $PKG NO_ISOLATED_STORAGE allow
cmd appops set       $PKG LEGACY_STORAGE      allow
cmd appops set       $PKG READ_EXTERNAL_STORAGE  allow
cmd appops set       $PKG WRITE_EXTERNAL_STORAGE allow

echo "appops now:"
cmd appops get $PKG | grep -iE 'ISOLATED|LEGACY|EXTERNAL_STORAGE'

# The description file must live where RealDash looks: it creates
# camera/ datalogs/ dyno/ settings/ trackdays/ tripdiary/ at install but
# NOT descfiles/, so that folder has to be made by hand.
mkdir -p /sdcard/Documents/RealDash/descfiles

echo
echo "descfiles:"
ls -la /sdcard/Documents/RealDash/descfiles/
echo
echo "Now force-stop RealDash so MediaProvider re-reads the op:"
echo "  am force-stop $PKG"
