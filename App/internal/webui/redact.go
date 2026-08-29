package webui

import (
	"regexp"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"
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
		message = redactWholeTokens(message, secret)
	}
	message = userinfoPattern.ReplaceAllString(message, "${1}"+redactedPlaceholder+"@")
	message = keyValueSecretPattern.ReplaceAllString(message, "${1}="+redactedPlaceholder)
	return message
}

// redactWholeTokens replaces every occurrence of secret that is not part of a longer Unicode
// letter/number run. It checks boundaries without consuming them, so adjacent occurrences such
// as "xy xy" and "xy,xy" are both removed in a single pass.
func redactWholeTokens(message, secret string) string {
	if secret == "" {
		return message
	}

	var matches [][2]int
	for offset := 0; offset <= len(message)-len(secret); {
		relative := strings.Index(message[offset:], secret)
		if relative < 0 {
			break
		}
		start := offset + relative
		end := start + len(secret)
		if isTokenBoundaryBefore(message, start) && isTokenBoundaryAfter(message, end) {
			matches = append(matches, [2]int{start, end})
			offset = end
			continue
		}
		_, size := utf8.DecodeRuneInString(message[start:])
		if size == 0 {
			size = 1
		}
		offset = start + size
	}
	if len(matches) == 0 {
		return message
	}

	var redacted strings.Builder
	redacted.Grow(len(message))
	previous := 0
	for _, match := range matches {
		redacted.WriteString(message[previous:match[0]])
		redacted.WriteString(redactedPlaceholder)
		previous = match[1]
	}
	redacted.WriteString(message[previous:])
	return redacted.String()
}

func isTokenBoundaryBefore(value string, byteOffset int) bool {
	if byteOffset == 0 {
		return true
	}
	r, _ := utf8.DecodeLastRuneInString(value[:byteOffset])
	return !unicode.IsLetter(r) && !unicode.IsNumber(r)
}

func isTokenBoundaryAfter(value string, byteOffset int) bool {
	if byteOffset == len(value) {
		return true
	}
	r, _ := utf8.DecodeRuneInString(value[byteOffset:])
	return !unicode.IsLetter(r) && !unicode.IsNumber(r)
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
	// go through whole-token matching instead of being replaced blindly. Count runes, not bytes:
	// a two-character password such as "éé" occupies four UTF-8 bytes but is still too short for
	// safe substring replacement.
	const substringSafeLength = 4
	seenLong := make(map[string]bool)
	seenShort := make(map[string]bool)

	for formKey, value := range submitted {
		if value == "" {
			continue
		}
		_, key, ok := strings.Cut(formKey, "_")
		if !ok || !sensitiveFormKeys[key] {
			continue
		}
		if utf8.RuneCountInString(value) >= substringSafeLength {
			if !seenLong[value] {
				long = append(long, value)
				seenLong[value] = true
			}
		} else if !seenShort[value] {
			short = append(short, value)
			seenShort[value] = true
		}
	}
	sort.Slice(long, func(i, j int) bool { return len(long[i]) > len(long[j]) })
	return long, short
}
