//go:build !(android && cmfa)

package dns

import (
	"sync/atomic"

	"github.com/metacubex/mihomo/component/resolver"
)

// nil uses native discovery; a published empty slice withdraws all managed DNS.
var systemResolver atomic.Pointer[[]dnsClient]

func UpdateSystemDNS(addr []string) {
	ns := make([]NameServer, 0, len(addr))
	for _, d := range addr {
		ns = append(ns, NameServer{Addr: d})
	}
	clients := transform(ns, nil)
	systemResolver.Store(&clients)
}

func FlushCacheWithDefaultResolver() {
	resolver.ClearCache()
	resolver.ResetConnection()
}
