FROM golang:1.21-alpine AS builder

WORKDIR /app

# Install eBPF dependencies for bpf2go
RUN apk add --no-cache clang llvm linux-headers libbpf-dev git

# Copy go mod and source files
COPY go.mod go.sum* ./
RUN go mod download || true

COPY . .

# Run go generate to compile the eBPF C program to Go bindings
RUN go get github.com/cilium/ebpf/cmd/bpf2go@v0.11.0
RUN go generate ./...

# Build the Go app
RUN CGO_ENABLED=0 GOOS=linux go build -o /semanticos-agent main.go

FROM alpine:latest

WORKDIR /

COPY --from=builder /semanticos-agent /semanticos-agent

# Run the agent
ENTRYPOINT ["/semanticos-agent"]
