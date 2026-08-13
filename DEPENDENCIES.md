# Dependencies

The core runtime has one direct third-party dependency:

- NumPy (`numpy>=1.24`) for compact bulk and residual arrays.

Everything else in the offline core uses the Python standard library. The
Gemini adapter is optional, offline by default, and uses standard-library HTTP
facilities; importing or using the core never contacts a remote service.

No VTE, LLM runtime, model weights, benchmark dataset or hosted API is required
by the core package.
