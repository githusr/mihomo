//go:build !(android && cmfa)

package dns

import (
	"context"
	"net"
	"sync"
	"testing"
	"time"

	D "github.com/miekg/dns"
)

func TestManagedSystemDNS(t *testing.T) {
	t.Cleanup(func() { systemResolver.Store(nil) })
	system := []NameServer{{Net: "system"}}
	main := NewResolver(Config{Main: system, DirectServer: system})
	policy := NewResolver(Config{Main: []NameServer{{Net: "rcode", Addr: "refused"}},
		Policy: []Policy{{Domain: "system-dns.test", NameServers: system}}})
	for _, answer := range []string{"192.0.2.10", "192.0.2.20"} {
		answer := answer // Go 1.20 shares range variables with the DNS handler.
		conn, err := net.ListenPacket("udp", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		server := &D.Server{PacketConn: conn, Handler: D.HandlerFunc(func(w D.ResponseWriter, query *D.Msg) {
			reply := new(D.Msg)
			reply.SetReply(query)
			reply.Answer = []D.RR{&D.A{Hdr: D.RR_Header{Name: query.Question[0].Name,
				Rrtype: D.TypeA, Class: D.ClassINET, Ttl: 60}, A: net.ParseIP(answer)}}
			_ = w.WriteMsg(reply)
		})}
		ready := make(chan struct{})
		server.NotifyStartedFunc = func() { close(ready) }
		go func() { _ = server.ActivateAndServe() }()
		<-ready
		t.Cleanup(func() { _ = server.Shutdown() })
		UpdateSystemDNS([]string{conn.LocalAddr().String()})
		for _, r := range []*Resolver{main.Resolver, main.DirectResolver, policy.Resolver} {
			r.ClearCache()
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			reply, err := r.ExchangeContext(ctx, new(D.Msg).SetQuestion("system-dns.test.", D.TypeA))
			cancel()
			if err != nil || reply == nil || len(reply.Answer) != 1 || reply.Answer[0].(*D.A).A.String() != answer {
				t.Fatalf("system resolver query = %v, %v; want %s", reply, err, answer)
			}
		}
	}
	UpdateSystemDNS(nil)
	clients, err := newSystemClient().getDnsClients()
	if err != nil || len(clients) != 0 || systemResolver.Load() == nil {
		t.Fatalf("empty snapshot fell back to native discovery: %v, %v", clients, err)
	}
}

func TestManagedSystemDNSConcurrentUpdate(t *testing.T) {
	t.Cleanup(func() { systemResolver.Store(nil) })
	UpdateSystemDNS(nil)
	var workers sync.WaitGroup
	for i := 0; i < 4; i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			client := newSystemClient()
			for j := 0; j < 100; j++ {
				_, _ = client.getDnsClients()
				_ = client.Address()
				client.ResetConnection()
			}
		}()
	}
	for i := 0; i < 100; i++ {
		UpdateSystemDNS([]string{"192.0.2.1:53", "[fe80::1%wlan0]:53"})
		UpdateSystemDNS(nil)
	}
	workers.Wait()
}
