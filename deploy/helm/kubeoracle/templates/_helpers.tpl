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
