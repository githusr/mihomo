package executor

import (
	"sync/atomic"

	"github.com/metacubex/mihomo/dns"
)

var systemDNSFlushPending atomic.Bool

// UpdateSystemDNS publishes immediately; provider downloads may hold mux for seconds.
func UpdateSystemDNS(servers []string) {
	dns.UpdateSystemDNS(servers)
	if !systemDNSFlushPending.CompareAndSwap(false, true) {
		return
	}
	go func() {
		mux.Lock()
		defer mux.Unlock()
		// Allow one subsequent refresh for updates arriving during this flush.
		systemDNSFlushPending.Store(false)
		dns.FlushCacheWithDefaultResolver()
	}()
}
