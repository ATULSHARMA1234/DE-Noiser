from datetime import datetime
import json
from sqlalchemy.orm import Session
from denoiser.storage.db import Span

def process_otlp_traces(db: Session, payload: dict):
    """
    Process OpenTelemetry HTTP JSON payload and store spans in the database.
    OTLP JSON format: https://opentelemetry.io/docs/specs/otlp/#json-protobuf-encoding
    """
    resource_spans = payload.get("resourceSpans", [])
    
    for rs in resource_spans:
        resource = rs.get("resource", {})
        attributes = resource.get("attributes", [])
        
        service_name = "unknown_service"
        for attr in attributes:
            if attr.get("key") == "service.name":
                val = attr.get("value", {})
                service_name = val.get("stringValue", service_name)
                
        scope_spans = rs.get("scopeSpans", [])
        for ss in scope_spans:
            spans = ss.get("spans", [])
            for span in spans:
                trace_id = span.get("traceId")
                span_id = span.get("spanId")
                parent_span_id = span.get("parentSpanId")
                name = span.get("name")
                
                # OTLP timestamps are in unix nanoseconds
                start_time_unix_nano = int(span.get("startTimeUnixNano", 0))
                end_time_unix_nano = int(span.get("endTimeUnixNano", 0))
                
                start_time = datetime.utcfromtimestamp(start_time_unix_nano / 1e9) if start_time_unix_nano else datetime.utcnow()
                end_time = datetime.utcfromtimestamp(end_time_unix_nano / 1e9) if end_time_unix_nano else datetime.utcnow()
                
                duration_ms = max(0, (end_time - start_time).total_seconds() * 1000.0)
                
                status_code = "OK"
                if "status" in span:
                    status_dict = span["status"]
                    code = status_dict.get("code")
                    if code == 2 or code == "STATUS_CODE_ERROR":
                        status_code = "ERROR"
                        
                span_attributes = {}
                for attr in span.get("attributes", []):
                    key = attr.get("key")
                    val_dict = attr.get("value", {})
                    if "stringValue" in val_dict:
                        span_attributes[key] = val_dict["stringValue"]
                    elif "intValue" in val_dict:
                        span_attributes[key] = val_dict["intValue"]
                    elif "boolValue" in val_dict:
                        span_attributes[key] = val_dict["boolValue"]
                    elif "doubleValue" in val_dict:
                        span_attributes[key] = val_dict["doubleValue"]
                        
                events = []
                for ev in span.get("events", []):
                    ev_time_nano = int(ev.get("timeUnixNano", 0))
                    ev_time = datetime.utcfromtimestamp(ev_time_nano / 1e9).isoformat() if ev_time_nano else datetime.utcnow().isoformat()
                    
                    ev_attrs = {}
                    for attr in ev.get("attributes", []):
                        key = attr.get("key")
                        val_dict = attr.get("value", {})
                        if "stringValue" in val_dict:
                            ev_attrs[key] = val_dict["stringValue"]
                            
                    events.append({
                        "name": ev.get("name"),
                        "timestamp": ev_time,
                        "attributes": ev_attrs
                    })
                    
                db_span = Span(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    service_name=service_name,
                    operation_name=name,
                    start_time=start_time,
                    end_time=end_time,
                    duration_ms=duration_ms,
                    status_code=status_code,
                    attributes=span_attributes,
                    events=events
                )
                db.add(db_span)
                
    db.commit()
