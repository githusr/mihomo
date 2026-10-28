//go:build linux

package systemdns

import (
	"bufio"
	"fmt"
	"net"
	"os"
	"os/exec"
	"runtime"
	"sync"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

func start(cmd *exec.Cmd, update func([]string), failed func(error)) (func(), error) {
	pair, err := unix.Socketpair(unix.AF_UNIX, unix.SOCK_STREAM|unix.SOCK_CLOEXEC, 0)
	if err != nil {
		return nil, fmt.Errorf("system DNS socketpair: %w", err)
	}
	parent := os.NewFile(uintptr(pair[0]), "system-dns-parent")
	child := os.NewFile(uintptr(pair[1]), "system-dns-child")
	defer child.Close()
	connection, err := net.FileConn(parent)
	_ = parent.Close()
	if err != nil {
		return nil, err
	}
	// stdin watches the parent's lifetime; fd 3 carries snapshots back to it.
	cmd.Stdin = child
	cmd.ExtraFiles = []*os.File{child}
	cmd.Stderr = os.Stderr
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true, Pdeathsig: syscall.SIGKILL}
	started, done := make(chan error, 1), make(chan error, 1)
	go func() {
		// PDEATHSIG follows the creating OS thread, which Go may otherwise retire.
		runtime.LockOSThread()
		defer runtime.UnlockOSThread()
		err := cmd.Start()
		started <- err
		if err == nil {
			done <- cmd.Wait()
		}
	}()
	if err = <-started; err != nil {
		_ = connection.Close()
		return nil, fmt.Errorf("start system DNS helper: %w", err)
	}
	_ = child.Close()
	stopping := make(chan struct{})
	var once sync.Once
	stop := func() {
		once.Do(func() {
			close(stopping)
			_ = connection.Close()
			select {
			case <-done:
			case <-time.After(2 * time.Second):
				_ = cmd.Process.Kill()
				<-done
			}
		})
	}
	_ = connection.SetReadDeadline(time.Now().Add(10 * time.Second))
	reader := bufio.NewReaderSize(connection, MaxFrame)
	read := func() ([]string, error) {
		frame, err := reader.ReadSlice('\n')
		if err != nil {
			return nil, err
		}
		return decodeSnapshot(frame)
	}
	servers, err := read()
	if err != nil {
		stop()
		return nil, fmt.Errorf("system DNS initial snapshot: %w", err)
	}
	update(servers)
	_ = connection.SetReadDeadline(time.Time{})
	go func() {
		for {
			next, err := read()
			if err != nil {
				select {
				case <-stopping:
					return
				default:
				}
				update(nil)
				failed(fmt.Errorf("system DNS channel failed: %w", err))
				return
			}
			// The observer also notifies when the network changes but its DNS does not.
			update(next)
		}
	}()
	return stop, nil
}
