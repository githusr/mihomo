//go:build !android || cmfa

package systemdns

func Start(home string, update func([]string), failed func(error)) (func(), error) {
	return func() {}, nil
}
