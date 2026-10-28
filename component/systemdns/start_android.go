//go:build !cmfa

package systemdns

import (
	_ "embed"
	"fmt"
	"os"
	"os/exec"
)

//go:embed helper.dex
var helperDex []byte

// Start observes physical-network DNS for a standalone Android root process.
// Callbacks must not block; later updates and failures run on the reader goroutine.
func Start(home string, update func([]string), failed func(error)) (func(), error) {
	if os.Geteuid() != 0 {
		return func() {}, nil
	}
	path, err := ensureHelper(home, helperDex)
	if err != nil {
		return nil, fmt.Errorf("system DNS helper file: %w", err)
	}
	cmd := exec.Command("/system/bin/app_process", "-Djava.class.path="+path, "/",
		"--nice-name=mihomo-system-dns", "mihomo.systemdns.SystemDnsHelper", "3")
	return start(cmd, update, failed)
}
