All three files are VALID UTF-8. The straddle files place a multibyte char exactly on a buffer boundary. willitload must NOT flag these as ENCODING_FALLBACK or DECODE_ERROR (false positive).
