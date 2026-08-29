package webui

import (
	"strings"

	"horizonmemory/connector/internal/sanitize"
)

const redactedPlaceholder = sanitize.Placeholder

// redactError makes a driver error safe to show or log. Only values from credential-bearing
// fields are supplied as exact secrets; the shared sanitizer also catches URL userinfo and
// password/token key-value forms that came from ambient configuration or driver reformatting.
func redactError(err error, submitted map[string]string) string {
	return sanitize.Error(err, submittedSecrets(submitted)...)
}

func redactMessage(message string, submitted map[string]string) string {
	return sanitize.Message(message, submittedSecrets(submitted)...)
}

func submittedSecrets(submitted map[string]string) []string {
	secrets := make([]string, 0, len(submitted))
	for formKey, value := range submitted {
		if value == "" {
			continue
		}
		_, key, ok := strings.Cut(formKey, "_")
		if ok && sensitiveFormKeys[key] {
			secrets = append(secrets, value)
		}
	}
	return secrets
}
