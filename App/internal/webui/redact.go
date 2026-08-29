package webui

import (
	"regexp"
	"sort"
	"strings"
)

// redactedPlaceholder replaces anything removed from an error message.
const redactedPlaceholder = "[redacted]"

// userinfoPattern matches the "user:password@" segment of a URL-shaped connection string.
// Deliberately narrow: it requires a scheme, so it cannot eat an ordinary "word:word@word" in
// prose, and it keeps the scheme and host so the message still says where the connection went.
var userinfoPattern = regexp.MustCompile(`([a-zA-Z][a-zA-Z0-9+.\-]*://)([^\s/@]+)@`)

// keyValueSecretPattern matches "password=..." style parameters as they appear in DSNs and in
// driver errors that echo a parsed config back (e.g. libpq's "password=hunter2 host=db").
var keyValueSecretPattern = regexp.MustCompile(
	`(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*("[^"]*"|'[^']*'|[^\s,;&)}\]]+)`)

// redactError makes a driver error safe to show or log.
//
// Connection errors routinely quote the connection string back: "failed to connect to
// `postgres://app:hunter2@db:5432`", a DSN parse error naming the offending parameter, a Mongo
// URI echoed whole. The form fields are already dropped before re-rendering the page, but that
// does nothing about the error text -- so a password submitted through the form could still reach
// the page, and the log, by way of the message.
//
// Two passes, because neither alone is sufficient. The submitted values are removed by exact
// match, which catches a secret wherever a driver chose to put it, in whatever surrounding text.
// The patterns then catch what the first pass cannot see: a credential the driver reformatted, or
// one that came from an environment variable rather than the form.
func redactError(err error, submitted map[string]string) string {
	if err == nil {
		return ""
	}
	return redactMessage(err.Error(), submitted)
}

func redactMessage(message string, submitted map[string]string) string {
	for _, secret := range secretValues(submitted) {
		message = strings.ReplaceAll(message, secret, redactedPlaceholder)
	}
	message = userinfoPattern.ReplaceAllString(message, "${1}"+redactedPlaceholder+"@")
	message = keyValueSecretPattern.ReplaceAllString(message, "${1}="+redactedPlaceholder)
	return message
}

// secretValues collects the submitted values that must never appear in output, longest first.
//
// Longest first matters: if a DSN and the password inside it are both submitted, replacing the
// password first would leave the DSN unmatched (its text no longer contains the password), and
// the rest of the DSN would survive. Replacing the longer DSN first removes both.
//
// Very short values are skipped -- a one or two character password would match everywhere in the
// message and turn it into placeholders, destroying the diagnostic without protecting anything
// the pattern pass does not already cover.
func secretValues(submitted map[string]string) []string {
	const minRedactableLength = 3

	var values []string
	for formKey, value := range submitted {
		if len(value) < minRedactableLength {
			continue
		}
		_, key, ok := strings.Cut(formKey, "_")
		if ok && sensitiveFormKeys[key] {
			values = append(values, value)
		}
	}
	sort.Slice(values, func(i, j int) bool { return len(values[i]) > len(values[j]) })
	return values
}
