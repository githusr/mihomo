# Android root system DNS

The standalone Android core starts a root `app_process` observer before loading
configuration. It supplies physical-network DNS addresses for `system` in
`nameserver`, `direct-nameserver`, `proxy-server-nameserver` and `nameserver-policy`.
DNS queries still run through Mihomo. No APK, companion app or configuration flag
is required. Non-root processes, other platforms, CMFA/JNI builds and tool
commands (`-v`, `-h`, `-t`, `generate`, `age`, `convert-ruleset`) keep their existing
behavior and do not extract or start the observer.

The observer requires Android 8/API 26 or later and permission to access
ConnectivityManager from the root SELinux domain. It ignores VPN networks,
prefers the active physical network, otherwise uses validated physical networks
or other eligible networks. IPv6 scope IDs are preserved. A change of DNS addresses
or selected physical networks invalidates Mihomo's DNS caches and resolver
connections, including handovers with identical DNS addresses. Empty snapshots
withdraw old DNS. Callbacks invalidate the current network scan, as in the source
PR; the observer does not retain startup networks that have disappeared.
The existing public-DNS fallback in the built-in system resolver is unchanged.
This discovers DNS server addresses; it does not delegate queries to Android's
Private DNS service. Existing root redirect rules must exempt the core's own DNS
traffic, just as with explicitly configured upstream DNS.

`<core-home>/system-dns-helper.dex` is verified against the embedded bytes and
reused or atomically replaced with mode 0400, without following symlinks. It is
retained along with ART's cache after exit. Existing home permissions are kept;
use a home directory trusted by root. The helper exchanges bounded, validated
JSON snapshots over an inherited anonymous socketpair. Parent EOF and PDEATHSIG
end the helper; normal shutdown waits up to two seconds before killing it.
Startup failure or an unexpected channel/service failure exits the core with
status 70, allowing its supervisor to restart it. A channel failure clears the
DNS snapshot before requesting shutdown.
Snapshot publication and failure notification never wait for configuration
reloads. Cache/connection refreshes are coalesced behind the configuration lock.
The ten-second forced-exit deadline starts on the reader goroutine, so startup
or SIGHUP provider downloads cannot postpone it.

## Building and checking

Install JDK 17+, Android SDK platform 36 and Build Tools 36.0.0, set
`ANDROID_HOME` (and `JAVA_HOME` if needed), then run:

```sh
python3 component/systemdns/test_observer.py
python3 component/systemdns/build.py
# Use the normal Android Go/NDK build after generating helper.dex.
go test -race ./component/systemdns ./dns ./hub/executor
```

The fork's workflow adapter inserts dex generation before the Android core build;
the feature patch leaves upstream workflows unchanged. Android builds first run
the actual Java observer against fake platform services to check same-DNS handovers,
duplicate callbacks, DNS changes, stale startup networks and VPN/offline selection.
The dex is not checked in. Go tests exercise real socketpair/process shutdown,
repeated-address handovers, malformed snapshots,
atomic dex caching, concurrent DNS updates and DNS queries through the main,
direct and policy resolvers. Host tests do not validate Android framework behavior.
On a rooted device, check system DNS queries, Wi-Fi/mobile switching, offline
withdrawal, handovers between networks with the same DNS addresses, helper death
(exit 70), core death (helper cleanup), and that tool
commands leave no helper/dex. Android/OEM SELinux policy and real IPv6 DNS require
device testing.

Adapted from [AndroidLibClashLite PR #2](https://github.com/Asterisk4Magisk/AndroidLibClashLite/pull/2)
by 5ec1cff and the AsteriskMETA contributors. The protocol and dex cache reuse
their LGPL-3.0 implementation; the Java observer retains its GPL-3.0 notice.
