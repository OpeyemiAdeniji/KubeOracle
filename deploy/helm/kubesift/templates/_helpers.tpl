{{- define "kubesift.name" -}}{{ .Release.Name }}-kubesift{{- end -}}

{{- define "kubesift.labels" -}}
app.kubernetes.io/name: kubesift
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "kubesift.selector" -}}
app.kubernetes.io/name: kubesift
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "kubesift.secretName" -}}{{ .Values.existingSecret | default (include "kubesift.name" .) }}{{- end -}}

{{/* External database configured: replicas are stateless and the SQLite volume is not used. */}}
{{- define "kubesift.externalDb" -}}{{ if or .Values.database.url (and .Values.existingSecret .Values.database.fromExistingSecret) }}true{{ end }}{{- end -}}

{{- define "kubesift.validate" -}}
{{- if and (gt (int .Values.replicaCount) 1) (not (include "kubesift.externalDb" .)) -}}
{{- fail "replicaCount > 1 needs a shared database: set database.url (PostgreSQL), or database.fromExistingSecret with a database-url key in existingSecret" -}}
{{- end -}}
{{- end -}}
