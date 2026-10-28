package systemdns

import (
	"reflect"
	"strings"
	"testing"
)

func TestSnapshots(t *testing.T) {
	for _, frame := range []string{
		`{"version":1,"servers":[]}`,
		`{"version":1,"servers":["192.0.2.1:53","[2001:db8::1]:53","[fe80::1%wlan0]:53"]}`,
	} {
		if _, err := decodeSnapshot([]byte(frame)); err != nil {
			t.Fatalf("valid snapshot: %v", err)
		}
	}
	for _, frame := range []string{
		`{"version":2,"servers":[]}`, `{"version":1}`, `{"version":1,"servers":null}`,
		`{"version":1,"servers":["example.com:53"]}`, `{"version":1,"servers":["192.0.2.1:5353"]}`,
		`{"version":1,"servers":["127.0.0.1:53"]}`, `{"version":1,"servers":["[::]:53"]}`,
		`{"version":1,"servers":["224.0.0.1:53"]}`, `{"version":1,"servers":["[ff02::1]:53"]}`,
		`{"version":1,"servers":["[::ffff:127.0.0.1]:53"]}`, `{"version":1,"servers":["[::ffff:0.0.0.0]:53"]}`,
		`{"version":1,"servers":["[::ffff:224.0.0.1]:53"]}`,
		`{"version":1,"servers":[` + strings.Repeat(`"192.0.2.1:53",`, 32) + `"192.0.2.1:53"]}`,
		`{"version":1,"servers":[]}` + strings.Repeat(" ", MaxFrame),
		`{"version":1,"servers":[]} {}`, `{"version":1,"servers":[],"query":"example.com"}`,
	} {
		if _, err := decodeSnapshot([]byte(frame)); err == nil {
			t.Fatalf("accepted invalid snapshot: %s", frame)
		}
	}
	got, err := decodeSnapshot([]byte(`{"version":1,"servers":["[2001:0db8::1]:53","[2001:db8::1]:53","192.0.2.1:53"]}`))
	if err != nil || !reflect.DeepEqual(got, []string{"[2001:db8::1]:53", "192.0.2.1:53"}) {
		t.Fatalf("snapshot normalization = %v, %v", got, err)
	}
}
