{{- define "semanticos.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- define "semanticos.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}
{{- define "semanticos.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "semanticos.labels" -}}
helm.sh/chart: {{ include "semanticos.chart" . }}
app.kubernetes.io/name: {{ include "semanticos.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "semanticos.secretName" -}}
{{- .Values.secrets.existingSecret | default (printf "%s-secrets" (include "semanticos.fullname" .)) -}}
{{- end }}

{{- define "semanticos.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "semanticos.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end }}

{{/*
Common environment block for every application pod. Secrets are referenced from
the managed/created Secret; non-sensitive config comes straight from values.
*/}}
{{- define "semanticos.env" -}}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "semanticos.secretName" . }}
      key: database-url
- name: JWT_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "semanticos.secretName" . }}
      key: jwt-secret-key
- name: SEMANTICOS_ADMIN_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "semanticos.secretName" . }}
      key: admin-password
- name: INGEST_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "semanticos.secretName" . }}
      key: ingest-api-key
- name: SCIM_BEARER_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "semanticos.secretName" . }}
      key: scim-bearer-token
- name: CLICKHOUSE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "semanticos.secretName" . }}
      key: clickhouse-password
- name: REDIS_URL
  value: {{ .Values.redis.url | quote }}
- name: CLICKHOUSE_HOST
  value: {{ .Values.clickhouse.host | quote }}
- name: CLICKHOUSE_PORT
  value: {{ .Values.clickhouse.port | quote }}
- name: CLICKHOUSE_USER
  value: {{ .Values.clickhouse.user | quote }}
- name: KAFKA_BROKER
  value: {{ .Values.kafka.broker | quote }}
- name: CORS_ALLOWED_ORIGINS
  value: {{ .Values.config.corsAllowedOrigins | quote }}
- name: SEMANTICOS_ENV
  value: {{ .Values.config.environment | quote }}
- name: SEMANTICOS_AUTO_MIGRATE
  value: {{ .Values.config.autoMigrate | quote }}
- name: SEMANTICOS_SCHEDULER_ENABLED
  value: {{ .Values.config.schedulerEnabled | quote }}
{{- end }}
