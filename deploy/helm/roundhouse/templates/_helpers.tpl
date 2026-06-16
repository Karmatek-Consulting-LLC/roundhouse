{{/* vim: set filetype=mustache: */}}

{{- define "roundhouse.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "roundhouse.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "roundhouse.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "roundhouse.labels" -}}
helm.sh/chart: {{ include "roundhouse.chart" . }}
{{ include "roundhouse.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "roundhouse.selectorLabels" -}}
app.kubernetes.io/name: {{ include "roundhouse.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* per-component names */}}
{{- define "roundhouse.api.fullname" -}}
{{ include "roundhouse.fullname" . }}-api
{{- end -}}

{{- define "roundhouse.postgres.fullname" -}}
{{ include "roundhouse.fullname" . }}-postgres
{{- end -}}

{{- define "roundhouse.api.image" -}}
{{ .Values.image.api.repository }}:{{ .Values.image.api.tag | default .Chart.AppVersion }}
{{- end -}}

{{/* DB env block — sourced from bundled Postgres or external. */}}
{{- define "roundhouse.dbEnv" -}}
- name: DB_CONNECTION
  value: pgsql
{{- if .Values.postgres.enabled }}
- name: DB_HOST
  value: {{ include "roundhouse.postgres.fullname" . | quote }}
- name: DB_PORT
  value: "5432"
- name: DB_DATABASE
  value: {{ .Values.postgres.auth.database | quote }}
- name: DB_USERNAME
  value: {{ .Values.postgres.auth.user | quote }}
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "roundhouse.fullname" . }}-db
      key: password
{{- else }}
- name: DB_HOST
  value: {{ required "externalDatabase.host is required when postgres.enabled=false" .Values.externalDatabase.host | quote }}
- name: DB_PORT
  value: {{ .Values.externalDatabase.port | quote }}
- name: DB_DATABASE
  value: {{ required "externalDatabase.database is required" .Values.externalDatabase.database | quote }}
- name: DB_USERNAME
  value: {{ required "externalDatabase.user is required" .Values.externalDatabase.user | quote }}
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ required "externalDatabase.passwordSecret is required" .Values.externalDatabase.passwordSecret | quote }}
      key: password
{{- end }}
{{- end -}}
