{{- define "kubeoracle.name" -}}{{ .Release.Name }}-kubeoracle{{- end -}}

{{- define "kubeoracle.labels" -}}
app.kubernetes.io/name: kubeoracle
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "kubeoracle.selector" -}}
app.kubernetes.io/name: kubeoracle
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "kubeoracle.secretName" -}}{{ .Values.existingSecret | default (include "kubeoracle.name" .) }}{{- end -}}

{{/* External database configured: replicas are stateless and the SQLite volume is not used. */}}
{{- define "kubeoracle.externalDb" -}}{{ if or .Values.database.url (and .Values.existingSecret .Values.database.fromExistingSecret) }}true{{ end }}{{- end -}}

{{- define "kubeoracle.validate" -}}
{{- if and (gt (int .Values.replicaCount) 1) (not (include "kubeoracle.externalDb" .)) -}}
{{- fail "replicaCount > 1 needs a shared database: set database.url (PostgreSQL), or database.fromExistingSecret with a database-url key in existingSecret" -}}
{{- end -}}
{{- end -}}
