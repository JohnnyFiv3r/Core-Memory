//! Validating the ID token a sign-in returns: its RS256 signature against
//! OpenAI's published keys, its issuer, its audience (the issued client),
//! its expiry and the nonce of this sign-in. Its `sub` is the account.
//!
//! Messages name what failed and never repeat the token or its claims.

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use ring::signature::{RSA_PKCS1_2048_8192_SHA256, RsaPublicKeyComponents};
use serde_json::Value;

/// Seconds a clock may be off before an expired token is refused.
const CLOCK_SKEW: i64 = 5;

/// What a valid ID token says of the account.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Identity {
    pub subject: String,
    pub email: Option<String>,
}

/// What the token must match.
#[derive(Debug, Clone, Copy)]
pub(crate) struct Expected<'a> {
    pub issuer: &'a str,
    pub client_id: &'a str,
    pub nonce: &'a str,
    /// Unix seconds.
    pub now: i64,
}

fn part(encoded: &str, what: &str) -> Result<Vec<u8>, String> {
    URL_SAFE_NO_PAD
        .decode(encoded)
        .map_err(|_| format!("its {what} is not base64url"))
}

fn json_part(encoded: &str, what: &str) -> Result<Value, String> {
    serde_json::from_slice(&part(encoded, what)?).map_err(|_| format!("its {what} is not JSON"))
}

/// Validates `token` against the key set `jwks` and `expected`.
pub(crate) fn validate(
    token: &str,
    jwks: &Value,
    expected: Expected<'_>,
) -> Result<Identity, String> {
    let mut parts = token.split('.');
    let (Some(header), Some(payload), Some(signature), None) =
        (parts.next(), parts.next(), parts.next(), parts.next())
    else {
        return Err("it is not a signed JWT".into());
    };
    let head = json_part(header, "header")?;
    // Only the algorithm OpenAI signs with. A token that names another,
    // `none` above all, is refused before any key is looked at.
    if head["alg"] != "RS256" {
        return Err("it is not signed with RS256".into());
    }
    let kid = head["kid"]
        .as_str()
        .filter(|k| !k.is_empty())
        .ok_or("it names no signing key")?;
    // Only the published key set is trusted, never a key or a key URL the
    // token itself names.
    let key = jwks["keys"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|k| {
            k["kid"] == kid
                && k["kty"] == "RSA"
                && (k["use"].is_null() || k["use"] == "sig")
                && (k["alg"].is_null() || k["alg"] == "RS256")
        })
        .ok_or("it is signed with a key OpenAI does not publish")?;
    let modulus = part(key["n"].as_str().unwrap_or_default(), "key's modulus")?;
    let exponent = part(key["e"].as_str().unwrap_or_default(), "key's exponent")?;
    let strip = |bytes: &[u8]| -> Vec<u8> {
        let first = bytes.iter().position(|b| *b != 0).unwrap_or(bytes.len());
        bytes[first..].to_vec()
    };
    let public = RsaPublicKeyComponents {
        n: strip(&modulus),
        e: strip(&exponent),
    };
    let signed = &token[..header.len() + 1 + payload.len()];
    public
        .verify(
            &RSA_PKCS1_2048_8192_SHA256,
            signed.as_bytes(),
            &part(signature, "signature")?,
        )
        .map_err(|_| "its signature does not match OpenAI's key")?;

    let claims = json_part(payload, "payload")?;
    if claims["iss"] != expected.issuer {
        return Err("it was not issued by OpenAI's sign-in".into());
    }
    let audience_ok = match &claims["aud"] {
        Value::String(aud) => aud == expected.client_id,
        Value::Array(auds) => auds.iter().any(|a| a == expected.client_id),
        _ => false,
    };
    if !audience_ok {
        return Err("it was not issued to this app's client".into());
    }
    let expires = claims["exp"].as_i64().ok_or("it has no expiry")?;
    if expires + CLOCK_SKEW < expected.now {
        return Err("it has expired".into());
    }
    if claims["nonce"] != expected.nonce {
        return Err("it was not issued for this sign-in (nonce)".into());
    }
    let subject = claims["sub"]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or("it names no account")?
        .to_string();
    let email = claims["email"].as_str().map(str::to_string);
    Ok(Identity { subject, email })
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use ring::rand::SystemRandom;
    use ring::signature::{RSA_PKCS1_SHA256, RsaKeyPair};
    use serde_json::json;

    /// A 2048-bit RSA key made for these tests and nothing else (PKCS#8).
    const TEST_KEY: &str = include_str!("../testdata/id-token-key.b64");

    fn key_pair() -> RsaKeyPair {
        let der = base64::engine::general_purpose::STANDARD
            .decode(TEST_KEY.split_whitespace().collect::<String>())
            .unwrap();
        RsaKeyPair::from_pkcs8(&der).unwrap()
    }

    /// The published key set, holding the test key as `kid`.
    pub(crate) fn jwks(kid: &str) -> Value {
        let public: RsaPublicKeyComponents<Vec<u8>> = key_pair().public().into();
        json!({"keys": [{
            "kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
            "n": URL_SAFE_NO_PAD.encode(&public.n),
            "e": URL_SAFE_NO_PAD.encode(&public.e),
        }]})
    }

    /// `claims` signed with the test key as `kid`.
    pub(crate) fn sign(kid: &str, claims: &Value) -> String {
        let header = URL_SAFE_NO_PAD.encode(json!({"alg": "RS256", "kid": kid}).to_string());
        let payload = URL_SAFE_NO_PAD.encode(claims.to_string());
        let input = format!("{header}.{payload}");
        let pair = key_pair();
        let mut signature = vec![0u8; pair.public().modulus_len()];
        pair.sign(
            &RSA_PKCS1_SHA256,
            &SystemRandom::new(),
            input.as_bytes(),
            &mut signature,
        )
        .unwrap();
        format!("{input}.{}", URL_SAFE_NO_PAD.encode(signature))
    }

    pub(crate) fn claims(issuer: &str, client: &str, nonce: &str, now: i64) -> Value {
        json!({
            "iss": issuer, "aud": client, "sub": "user-123", "email": "user@example.com",
            "nonce": nonce, "iat": now, "exp": now + 3600,
        })
    }

    const ISSUER: &str = "https://auth.openai.com";
    const NOW: i64 = 1_795_000_000;

    fn expected() -> Expected<'static> {
        Expected {
            issuer: ISSUER,
            client_id: "oaiapp_abc",
            nonce: "nonce-1",
            now: NOW,
        }
    }

    #[test]
    fn a_token_signed_by_the_published_key_names_the_account() {
        let token = sign("k1", &claims(ISSUER, "oaiapp_abc", "nonce-1", NOW));
        let identity = validate(&token, &jwks("k1"), expected()).unwrap();
        assert_eq!(identity.subject, "user-123");
        assert_eq!(identity.email.as_deref(), Some("user@example.com"));
        // An audience given as a list holding the client is accepted.
        let mut listed = claims(ISSUER, "oaiapp_abc", "nonce-1", NOW);
        listed["aud"] = json!(["oaiapp_abc"]);
        validate(&sign("k1", &listed), &jwks("k1"), expected()).unwrap();
    }

    #[test]
    fn every_claim_and_the_signature_are_checked() {
        let refuse = |token: &str, jwks: &Value| validate(token, jwks, expected()).unwrap_err();
        let good = claims(ISSUER, "oaiapp_abc", "nonce-1", NOW);
        let with = |key: &str, value: Value| {
            let mut c = good.clone();
            c[key] = value;
            sign("k1", &c)
        };
        assert!(
            refuse(&with("iss", json!("https://evil.example")), &jwks("k1")).contains("issued by")
        );
        assert!(refuse(&with("aud", json!("oaiapp_other")), &jwks("k1")).contains("client"));
        assert!(refuse(&with("exp", json!(NOW - 60)), &jwks("k1")).contains("expired"));
        assert!(refuse(&with("nonce", json!("nonce-2")), &jwks("k1")).contains("nonce"));
        assert!(refuse(&with("sub", json!("")), &jwks("k1")).contains("account"));
        // A key the published set does not hold.
        let token = sign("k1", &good);
        assert!(refuse(&token, &jwks("k2")).contains("does not publish"));
        // A payload changed after signing.
        let mut parts: Vec<&str> = token.split('.').collect();
        let forged = URL_SAFE_NO_PAD.encode(
            claims(ISSUER, "oaiapp_abc", "nonce-1", NOW)
                .to_string()
                .replace("user-123", "user-999"),
        );
        parts[1] = &forged;
        assert!(refuse(&parts.join("."), &jwks("k1")).contains("signature"));
        // An unsigned token.
        let none = format!(
            "{}.{}.",
            URL_SAFE_NO_PAD.encode(r#"{"alg":"none","kid":"k1"}"#),
            parts[1]
        );
        assert!(refuse(&none, &jwks("k1")).contains("RS256"));
        assert!(refuse("not-a-jwt", &jwks("k1")).contains("JWT"));
    }

    #[test]
    fn a_refusal_never_repeats_the_token() {
        let token = sign("k1", &claims(ISSUER, "oaiapp_other", "nonce-1", NOW));
        let message = validate(&token, &jwks("k1"), expected()).unwrap_err();
        assert!(!message.contains(&token[..20]), "{message}");
        assert!(!message.contains("user@example.com"), "{message}");
    }
}
