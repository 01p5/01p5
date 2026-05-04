{{- define "olympus.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "olympus.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "olympus.serviceAccountName" -}}
{{- if .Values.serviceAccount.name -}}
{{ .Values.serviceAccount.name }}
{{- else -}}
{{ printf "%s-agent" .Release.Name }}
{{- end -}}
{{- end -}}

{{- define "olympus.secretName" -}}
{{- if .Values.secrets.secretName -}}
{{ .Values.secrets.secretName }}
{{- else -}}
{{ printf "%s-secrets" .Release.Name }}
{{- end -}}
{{- end -}}
