package exporter

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"net/http"

	"github.com/semanticos/agent/ecs"
)

type ClickHouseExporter struct {
	Endpoint string
	APIKey   string
}

func NewClickHouseExporter(endpoint, apiKey string) *ClickHouseExporter {
	return &ClickHouseExporter{
		Endpoint: endpoint,
		APIKey:   apiKey,
	}
}

// SendBatch sends a batch of ECS events to the SemanticOS ingest endpoint
func (e *ClickHouseExporter) SendBatch(events []ecs.ECSEvent) error {
	if len(events) == 0 {
		return nil
	}

	payload := map[string]interface{}{
		"source": "ebpf_agent",
		"logs":   events,
	}

	jsonData, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("failed to marshal events: %w", err)
	}

	req, err := http.NewRequest("POST", e.Endpoint, bytes.NewBuffer(jsonData))
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	if e.APIKey != "" {
		req.Header.Set("X-API-Key", e.APIKey)
	}

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("failed to send request: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return fmt.Errorf("received non-200 response: %d", resp.StatusCode)
	}

	log.Printf("Successfully exported %d eBPF events to %s", len(events), e.Endpoint)
	return nil
}
