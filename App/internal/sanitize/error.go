// Package sanitize removes credentials from errors before they reach a UI, terminal, or log.
package sanitize

import (
	"regexp"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"
)

// Placeholder replaces anything removed from an error message.
const Placeholder = "[redacted]"

var userinfoPattern = regexp.MustCompile(`([a-zA-Z][a-zA-Z0-9+.\-]*://)([^\s/@]+)@`)

var keyValueSecretPattern = regexp.MustCompile(
	`(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*("[^"]*"|'[^']*'|[^\s,;&)}\]]+)`)

// Error returns a safe rendering of err. Exact submitted/configured secrets are removed first;
// URL userinfo and key/value credential patterns then catch reformatted or ambient values.
func Error(err error, secrets ...string) string {
	if err == nil {
		return ""
	}
	return Message(err.Error(), secrets...)
}

// Message redacts a diagnostic while preserving its non-secret context.
func Message(message string, secrets ...string) string {
	long, short := splitSecrets(secrets)
	for _, secret := range long {
		message = strings.ReplaceAll(message, secret, Placeholder)
	}
	for _, secret := range short {
		message = redactWholeTokens(message, secret)
	}
	message = userinfoPattern.ReplaceAllString(message, "${1}"+Placeholder+"@")
	message = keyValueSecretPattern.ReplaceAllStringFunc(message, func(match string) string {
		// An exact short-secret replacement may already have produced password=[redacted]. Do
		// not treat the placeholder itself as another secret and leave its closing bracket behind.
		// The unquoted-value branch deliberately stops before ']', so match ends at
		// "[redacted" while the final bracket remains just outside it.
		if strings.HasSuffix(match, strings.TrimSuffix(Placeholder, "]")) {
			return match
		}
		return keyValueSecretPattern.ReplaceAllString(match, "${1}="+Placeholder)
	})
	return message
}

func splitSecrets(values []string) (long, short []string) {
	const substringSafeLength = 4
	seenLong := make(map[string]bool)
	seenShort := make(map[string]bool)
	for _, value := range values {
		if value == "" {
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
		redacted.WriteString(Placeholder)
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
