{{/*
Expand the name of the chart.
*/}}
{{- define "ignition-edge.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
FORCED to .Release.Name for stable FQDN-based proxy routing.
The EmberNET dashboard proxy constructs the service FQDN as:
  {release-name}.{namespace}.svc.cluster.local
The service name MUST equal the release name (CHART-CONTRACT §3.2).
*/}}
{{- define "ignition-edge.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "ignition-edge.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "ignition-edge.labels" -}}
helm.sh/chart: {{ include "ignition-edge.chart" . }}
{{ include "ignition-edge.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "ignition-edge.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ignition-edge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app: {{ include "ignition-edge.fullname" . }}
{{- end }}

{{/*
EmberNET App Store discovery labels — THE BIG FIVE.
These go on pod templates AND services. All five. Always.
Miss one and your app is invisible to the dashboard.

embernet.ai/chart-name is used by the dashboard to resolve the
correct icon from the Helm repo index.
*/}}
{{- define "ignition-edge.storeLabels" -}}
embernet.ai/store-app: "true"
embernet.ai/gui-type: {{ .Values.embernet.guiType | default "web" | quote }}
embernet.ai/app-name: {{ .Values.embernet.appName | default "Ignition Edge" | quote }}
embernet.ai/chart-name: {{ .Chart.Name | quote }}
embernet.ai/gui-port: {{ .Values.embernet.guiPort | default "8088" | quote }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "ignition-edge.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "ignition-edge.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
