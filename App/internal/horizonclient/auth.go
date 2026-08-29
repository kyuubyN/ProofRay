package horizonclient

import (
	"encoding/json"
	"log"
	"os"
	"path/filepath"
	"runtime"
)

// resolveToken finds the bearer token api/server.py now requires on every request except
// GET /v1/health (api/machine_auth.py, added after this client was first written). This package
// only ever reads that token -- api/machine_auth.py's ensure_local_credentials is the sole writer,
// so a missing file here just means the API server has never been started yet, not something this
// client should fix by generating its own.
func resolveToken() string {
	if v := os.Getenv("PROOFRAY_API_TOKEN"); v != "" {
		return v
	}
	if v := os.Getenv("HORIZON_API_TOKEN"); v != "" {
		return v
	}

	path := credentialsPath()
	if info, err := os.Stat(path); err == nil {
		warnIfWorldReadable(path, info)
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	var credentials struct {
		Token string `json:"token"`
	}
	if err := json.Unmarshal(raw, &credentials); err != nil {
		return ""
	}
	return credentials.Token
}

// credentialsPath mirrors api/machine_auth.py's credentials_path(): an explicit override, else an
// OS-appropriate config directory, preferring the post-rebrand "proofray" path but falling back to
// the pre-rebrand "horizon-memory" one when only that file exists.
func credentialsPath() string {
	if v := os.Getenv("PROOFRAY_API_CREDENTIALS_PATH"); v != "" {
		return v
	}
	if v := os.Getenv("HORIZON_API_CREDENTIALS_PATH"); v != "" {
		return v
	}

	var base string
	if runtime.GOOS == "windows" {
		base = os.Getenv("APPDATA")
		if base == "" {
			home, _ := os.UserHomeDir()
			base = filepath.Join(home, "AppData", "Roaming")
		}
	} else {
		base = os.Getenv("XDG_CONFIG_HOME")
		if base == "" {
			home, err := os.UserHomeDir()
			if err == nil {
				base = filepath.Join(home, ".config")
			}
		}
	}

	proofrayPath := filepath.Join(base, "proofray", "api_credentials.json")
	if _, err := os.Stat(proofrayPath); err == nil {
		return proofrayPath
	}
	legacyPath := filepath.Join(base, "horizon-memory", "api_credentials.json")
	if _, err := os.Stat(legacyPath); err == nil {
		log.Printf("horizonclient: using pre-rebrand credentials at %s (no file found at %s); "+
			"if the token was rotated since, this stale one will fail auth", legacyPath, proofrayPath)
		return legacyPath
	}
	return proofrayPath
}

// warnIfWorldReadable flags a credentials file readable by users other than its owner. It only
// warns -- api/machine_auth.py writes the file at 0600 itself, so a looser mode here means
// something else on the machine (an umask, a manual chmod) loosened it after the fact.
func warnIfWorldReadable(path string, info os.FileInfo) {
	if runtime.GOOS == "windows" {
		return // Windows ACLs don't map onto the POSIX permission bits checked below.
	}
	if info.Mode().Perm()&0o077 != 0 {
		log.Printf("horizonclient: credentials file %s is readable by users other than its owner "+
			"(mode %o); consider chmod 600", path, info.Mode().Perm())
	}
}
