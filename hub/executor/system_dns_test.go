//go:build !(android && cmfa)

package executor

import (
	"sync"
	"testing"
	"time"

	"github.com/metacubex/mihomo/component/resolver"
	"github.com/metacubex/mihomo/dns"
)

type systemDNSRefreshProbe struct {
	resolver.Resolver
	cleared chan struct{}
	reset   chan struct{}
}

func (p *systemDNSRefreshProbe) ClearCache()      { p.cleared <- struct{}{} }
func (p *systemDNSRefreshProbe) ResetConnection() { p.reset <- struct{}{} }

func (p *systemDNSRefreshProbe) wait(t *testing.T) {
	t.Helper()
	for _, done := range []chan struct{}{p.cleared, p.reset} {
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			t.Fatal("DNS cache/connection refresh was lost")
		}
	}
}

func TestSystemDNSDoesNotWaitForConfig(t *testing.T) {
	probe := &systemDNSRefreshProbe{cleared: make(chan struct{}, 1), reset: make(chan struct{}, 1)}
	previous := resolver.DefaultResolver
	t.Cleanup(func() {
		mux.Lock()
		resolver.DefaultResolver = previous
		mux.Unlock()
	})
	mux.Lock()
	resolver.DefaultResolver = probe
	updated := make(chan struct{})
	go func() {
		for i := 0; i < 1000; i++ {
			UpdateSystemDNS([]string{"192.0.2.1:53"})
		}
		UpdateSystemDNS(nil) // A helper failure must withdraw DNS without waiting for mux.
		close(updated)
	}()
	select {
	case <-updated:
		mux.Unlock()
	case <-time.After(5 * time.Second):
		mux.Unlock()
		<-updated
		t.Fatal("DNS updates/failure withdrawal waited for configuration to finish")
	}
	probe.wait(t)
	UpdateSystemDNS(nil)
	probe.wait(t) // Coalescing must still allow the next network change to flush.
}

func TestSystemDNSDuringResolverReplacement(t *testing.T) {
	previous := resolver.DefaultResolver
	t.Cleanup(func() {
		mux.Lock()
		resolver.DefaultResolver = previous
		mux.Unlock()
	})
	var updates sync.WaitGroup
	updates.Add(1)
	go func() {
		defer updates.Done()
		for i := 0; i < 100; i++ {
			UpdateSystemDNS(nil)
		}
	}()
	for i := 0; i < 100; i++ {
		mux.Lock()
		resolver.DefaultResolver = dns.NewResolver(dns.Config{})
		mux.Unlock()
	}
	updates.Wait()
	probe := &systemDNSRefreshProbe{cleared: make(chan struct{}, 1), reset: make(chan struct{}, 1)}
	mux.Lock()
	resolver.DefaultResolver = probe
	UpdateSystemDNS(nil)
	mux.Unlock()
	probe.wait(t)
}
