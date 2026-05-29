package ecs

import (
	"time"
)

type ECSEvent struct {
	Timestamp string   `json:"@timestamp"`
	Event     Event    `json:"event"`
	Network   *Network `json:"network,omitempty"`
	Host      Host     `json:"host"`
	Process   *Process `json:"process,omitempty"`
}

type Process struct {
	PID         int    `json:"pid"`
	Name        string `json:"name"`
	Executable  string `json:"executable"`
}

type Event struct {
	Kind     string `json:"kind"`
	Category string `json:"category"`
	Type     string `json:"type"`
	Dataset  string `json:"dataset"`
	Action   string `json:"action,omitempty"`
}

type Network struct {
	Transport string `json:"transport,omitempty"`
	Protocol  string `json:"protocol,omitempty"`
	Direction string `json:"direction,omitempty"`
}

type Host struct {
	Hostname string `json:"hostname"`
	OS       OS     `json:"os"`
}

type OS struct {
	Platform string `json:"platform"`
	Family   string `json:"family"`
}

// MapKernelDrop creates an ECS event from a kernel packet drop
func MapKernelDrop(protocol string) ECSEvent {
	return ECSEvent{
		Timestamp: time.Now().UTC().Format(time.RFC3339Nano),
		Event: Event{
			Kind:     "event",
			Category: "network",
			Type:     "denied",
			Dataset:  "ebpf.network",
			Action:   "drop",
		},
		Network: &Network{
			Transport: protocol,
		},
		Host: Host{
			Hostname: "semanticos-node",
			OS: OS{
				Platform: "linux",
				Family:   "alpine",
			},
		},
	}
}

// MapKernelExecve creates an ECS event from a kernel sys_enter_execve
func MapKernelExecve(pid int, comm interface{}, filename interface{}) ECSEvent {
	// Simple type assertions depending on bpf2go generation ([16]int8 vs []byte etc.)
	commStr := ""
	switch v := comm.(type) {
	case [16]int8:
		commStr = int8SliceToString(v[:])
	case [16]uint8:
		commStr = byteSliceToString(v[:])
	}

	fileStr := ""
	switch v := filename.(type) {
	case [128]int8:
		fileStr = int8SliceToString(v[:])
	case [128]uint8:
		fileStr = byteSliceToString(v[:])
	}

	return ECSEvent{
		Timestamp: time.Now().UTC().Format(time.RFC3339Nano),
		Event: Event{
			Kind:     "event",
			Category: "process",
			Type:     "start",
			Dataset:  "ebpf.process",
			Action:   "execve",
		},
		Host: Host{
			Hostname: "semanticos-node",
			OS: OS{
				Platform: "linux",
				Family:   "alpine",
			},
		},
		Process: &Process{
			PID:        pid,
			Name:       commStr,
			Executable: fileStr,
		},
	}
}

func int8SliceToString(arr []int8) string {
	b := make([]byte, 0, len(arr))
	for _, v := range arr {
		if v == 0 {
			break
		}
		b = append(b, byte(v))
	}
	return string(b)
}

func byteSliceToString(arr []uint8) string {
	b := make([]byte, 0, len(arr))
	for _, v := range arr {
		if v == 0 {
			break
		}
		b = append(b, v)
	}
	return string(b)
}
