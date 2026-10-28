//go:build linux

package systemdns

import (
	"fmt"
	"io"
	"os"
	"os/exec"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"
)

// Run in a subprocess to exercise real inherited descriptors and parent EOF.
func TestSystemDNSProcess(t *testing.T) {
	mode, child := strings.CutPrefix(os.Args[len(os.Args)-1], "--system-dns-child=")
	if !child {
		return
	}
	output := os.NewFile(3, "snapshots")
	if mode == "bad-start" {
		fmt.Fprintln(output, `{"version":2,"servers":[]}`)
		os.Exit(0)
	}
	fmt.Fprintln(output, `{"version":1,"servers":["192.0.2.1:53"]}`)
	switch mode {
	case "changes":
		// A new physical network can advertise the same DNS endpoints.
		fmt.Fprintln(output, `{"version":1,"servers":["192.0.2.1:53"]}`)
		fmt.Fprintln(output, `{"version":1,"servers":[]}`)
		fmt.Fprintln(output, `{"version":1,"servers":["[fe80::1%wlan0]:53"]}`)
	case "disconnect":
		os.Exit(0)
	case "invalid":
		fmt.Fprintln(output, `{"version":1,"servers":["127.0.0.1:53"]}`)
	case "oversized":
		fmt.Fprint(output, strings.Repeat(" ", MaxFrame))
	}
	_, _ = io.Copy(io.Discard, os.Stdin)
	os.Exit(0)
}

func helperCommand(mode string) *exec.Cmd {
	return exec.Command(os.Args[0], "-test.run=^TestSystemDNSProcess$", "--", "--system-dns-child="+mode)
}

func TestClientUpdatesAndShutdown(t *testing.T) {
	updates := make(chan []string, 8)
	failed := make(chan error, 1)
	cmd := helperCommand("changes")
	stop, err := start(cmd, func(servers []string) { updates <- servers }, func(err error) { failed <- err })
	if err != nil {
		t.Fatal(err)
	}
	defer stop()
	for _, want := range [][]string{{"192.0.2.1:53"}, {"192.0.2.1:53"}, {}, {"[fe80::1%wlan0]:53"}} {
		select {
		case got := <-updates:
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("snapshot = %v, want %v", got, want)
			}
		case <-time.After(5 * time.Second):
			t.Fatal("missing DNS change")
		}
	}
	stop()
	stop()
	if !cmd.ProcessState.Success() {
		t.Fatalf("helper did not exit on stdin EOF: %v", cmd.ProcessState)
	}
	select {
	case err := <-failed:
		t.Fatalf("normal shutdown reported failure: %v", err)
	default:
	}
}

func TestClientWithdrawsDNSOnFailure(t *testing.T) {
	for _, mode := range []string{"disconnect", "invalid", "oversized"} {
		t.Run(mode, func(t *testing.T) {
			updates := make(chan []string, 8)
			failed := make(chan error, 1)
			stop, err := start(helperCommand(mode), func(servers []string) { updates <- servers }, func(err error) { failed <- err })
			if err != nil {
				t.Fatal(err)
			}
			defer stop()
			select {
			case err := <-failed:
				if err == nil || len(<-updates) != 1 || <-updates != nil {
					t.Fatal("helper failure did not withdraw DNS")
				}
			case <-time.After(5 * time.Second):
				t.Fatal("helper failure was not reported")
			}
		})
	}
}

func TestClientStartupFailure(t *testing.T) {
	for _, cmd := range []*exec.Cmd{exec.Command("/nonexistent/system-dns-helper"), helperCommand("bad-start")} {
		_, err := start(cmd, func([]string) { t.Error("invalid initial snapshot was applied") },
			func(error) { t.Error("startup failure reported as runtime failure") })
		if err == nil {
			t.Fatal("startup succeeded")
		}
		if cmd.Process != nil && cmd.ProcessState == nil {
			t.Fatal("failed helper was not reaped")
		}
	}
}

func TestClientKillsUnresponsiveHelper(t *testing.T) {
	cmd := helperCommand("idle")
	stop, err := start(cmd, func([]string) {}, func(error) {})
	if err != nil {
		t.Fatal(err)
	}
	defer stop()
	if err := cmd.Process.Signal(syscall.SIGSTOP); err != nil {
		t.Fatal(err)
	}
	stop()
	if cmd.ProcessState.Sys().(syscall.WaitStatus).Signal() != syscall.SIGKILL {
		t.Fatal("unresponsive helper was not killed")
	}
}
