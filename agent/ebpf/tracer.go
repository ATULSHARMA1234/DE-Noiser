package ebpf

//go:generate go run github.com/cilium/ebpf/cmd/bpf2go -type bpf_execve_event bpf c/tracer.c -- -I/usr/include/bpf

import (
	"bytes"
	"context"
	"encoding/binary"
	"fmt"
	"log"

	"github.com/cilium/ebpf/link"
	"github.com/cilium/ebpf/ringbuf"
	"github.com/semanticos/agent/ecs"
)

// Tracer attaches eBPF programs and processes ringbuffer events
type Tracer struct {
	eventsChan chan ecs.ECSEvent
	objs       bpfObjects
	links      []link.Link
}

func NewTracer(events chan ecs.ECSEvent) *Tracer {
	return &Tracer{
		eventsChan: events,
	}
}

// Start begins tracing real kernel events
func (t *Tracer) Start(ctx context.Context) error {
	log.Println("Starting real eBPF tracer...")

	// Load pre-compiled programs and maps into the kernel
	if err := loadBpfObjects(&t.objs, nil); err != nil {
		return fmt.Errorf("loading bpf objects: %v", err)
	}

	// Attach tracepoint
	tp, err := link.Tracepoint("syscalls", "sys_enter_execve", t.objs.TracepointSyscallsSysEnterExecve, nil)
	if err != nil {
		return fmt.Errorf("opening tracepoint: %v", err)
	}
	t.links = append(t.links, tp)

	// Open ringbuffer reader
	rd, err := ringbuf.NewReader(t.objs.Events)
	if err != nil {
		return fmt.Errorf("opening ringbuf reader: %v", err)
	}
	defer rd.Close()

	log.Println("eBPF tracer attached successfully. Listening for execve events...")

	go func() {
		<-ctx.Done()
		log.Println("Stopping eBPF tracer...")
		rd.Close()
		for _, l := range t.links {
			l.Close()
		}
		t.objs.Close()
	}()

	// Read events
	for {
		record, err := rd.Read()
		if err != nil {
			if err == ringbuf.ErrClosed {
				return nil
			}
			log.Printf("reading from ringbuf: %v", err)
			continue
		}

		// Parse the execve_event
		var e bpfBpfExecveEvent
		if err := binary.Read(bytes.NewBuffer(record.RawSample), binary.LittleEndian, &e); err != nil {
			log.Printf("parsing event: %v", err)
			continue
		}

		// Convert to ECS and send
		t.eventsChan <- ecs.MapKernelExecve(int(e.Pid), e.Comm, e.Filename)
	}
}
