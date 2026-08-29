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
	long, short := secretValues(submitted)
	for _, secret := range long {
		message = strings.ReplaceAll(message, secret, redactedPlaceholder)
	}
	// A very short secret is matched only as a whole token. Replacing every occurrence of "xy"
	// would also hit "proxy" and shred the message, but skipping short values entirely would
	// leak them -- and a weak password is still the visitor's password.
	for _, secret := range short {
		message = wholeTokenPattern(secret).ReplaceAllString(message, "${1}"+redactedPlaceholder+"${2}")
	}
	message = userinfoPattern.ReplaceAllString(message, "${1}"+redactedPlaceholder+"@")
	message = keyValueSecretPattern.ReplaceAllString(message, "${1}="+redactedPlaceholder)
	return message
}

// wholeTokenPattern matches secret only where it is not part of a longer alphanumeric run, so a
// two-character password cannot be redacted out of the middle of an unrelated word.
func wholeTokenPattern(secret string) *regexp.Regexp {
	return regexp.MustCompile(`([^\p{L}\p{N}]|^)` + regexp.QuoteMeta(secret) + `([^\p{L}\p{N}]|$)`)
}

// secretValues splits the submitted secrets into those safe to remove as plain substrings and
// those short enough to need whole-token matching. Nothing is discarded: every submitted secret
// is redacted one way or the other.
//
// Longest first matters for the substring group: if a DSN and the password inside it are both
// submitted, replacing the password first would leave the DSN unmatched (its text no longer
// contains the password) and the rest of the DSN would survive. Replacing the longer DSN first
// removes both.
func secretValues(submitted map[string]string) (long, short []string) {
	// Below this length a substring replacement starts hitting unrelated words, so those values
	// go through wholeTokenPattern instead of being replaced blindly.
	const substringSafeLength = 4

	for formKey, value := range submitted {
		if value == "" {
			continue
		}
		_, key, ok := strings.Cut(formKey, "_")
		if !ok || !sensitiveFormKeys[key] {
			continue
		}
		if len(value) >= substringSafeLength {
			long = append(long, value)
		} else {
			short = append(short, value)
		}
	}
	sort.Slice(long, func(i, j int) bool { return len(long[i]) > len(long[j]) })
	return long, short
}
