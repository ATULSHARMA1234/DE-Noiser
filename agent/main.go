package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/semanticos/agent/ebpf"
	"github.com/semanticos/agent/ecs"
	"github.com/semanticos/agent/exporter"
)

func main() {
	endpoint := flag.String("endpoint", "http://localhost:8000/ingest", "SemanticOS Ingest API Endpoint")
	apiKey := flag.String("api-key", "semanticos-ingest-key-123", "Ingest API Key")
	flag.Parse()

	log.Printf("Starting SemanticOS eBPF Agent. Targeting %s", *endpoint)

	eventsChan := make(chan ecs.ECSEvent, 1000)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Initialize Tracer
	tracer := ebpf.NewTracer(eventsChan)
	go func() {
		if err := tracer.Start(ctx); err != nil {
			log.Fatalf("eBPF Tracer failed: %v", err)
		}
	}()

	// Initialize Exporter
	exp := exporter.NewClickHouseExporter(*endpoint, *apiKey)

	// Batching Loop
	go func() {
		var batch []ecs.ECSEvent
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()

		for {
			select {
			case <-ctx.Done():
				// Flush remaining
				exp.SendBatch(batch)
				return
			case event := <-eventsChan:
				batch = append(batch, event)
				if len(batch) >= 100 {
					exp.SendBatch(batch)
					batch = batch[:0]
				}
			case <-ticker.C:
				if len(batch) > 0 {
					exp.SendBatch(batch)
					batch = batch[:0]
				}
			}
		}
	}()

	// Wait for interrupt
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)
	<-sigChan

	log.Println("Shutting down agent...")
	cancel()
	time.Sleep(1 * time.Second) // allow flushing
	log.Println("Agent exited.")
}
