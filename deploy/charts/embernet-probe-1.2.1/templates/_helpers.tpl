{{/*
Expand the name of the chart.
*/}}
{{- define "embernet-probe.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
FORCED to .Release.Name for stable FQDN-based proxy routing.
The EmberNET dashboard proxy constructs the service FQDN as:
  {release-name}.{namespace}.svc.cluster.local
The service name MUST equal the release name.
*/}}
{{- define "embernet-probe.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "embernet-probe.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "embernet-probe.labels" -}}
helm.sh/chart: {{ include "embernet-probe.chart" . }}
{{ include "embernet-probe.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "embernet-probe.selectorLabels" -}}
app.kubernetes.io/name: {{ include "embernet-probe.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app: {{ include "embernet-probe.fullname" . }}
{{- end }}

{{/*
EmberNET App Store discovery labels — THE BIG FIVE.
These go on pod templates AND services. All five. Always.
Miss one and your app is invisible to the dashboard.

embernet.ai/chart-name is used by the dashboard to resolve the
correct icon from the Helm repo index.
*/}}
{{- define "embernet-probe.storeLabels" -}}
embernet.ai/store-app: "true"
embernet.ai/gui-type: {{ .Values.gui.type | default "none" | quote }}
embernet.ai/app-name: {{ include "embernet-probe.name" . | quote }}
embernet.ai/chart-name: {{ .Chart.Name | quote }}
embernet.ai/gui-port: {{ .Values.gui.port | default .Values.service.port | quote }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "embernet-probe.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "embernet-probe.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
