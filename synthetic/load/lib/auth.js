import http from "k6/http";

export function mintAccessToken(config) {
  const response = http.post(
    `${config.keycloakUrl}/realms/${config.realm}/protocol/openid-connect/token`,
    {
      grant_type: "password",
      client_id: config.clientId,
      client_secret: config.clientSecret,
      username: config.username,
      password: config.password,
    },
    { tags: { endpoint: "keycloak-token" }, timeout: "10s" },
  );
  if (response.status !== 200) {
    throw new Error(`Keycloak token request failed with HTTP ${response.status}`);
  }
  const payload = response.json();
  if (
    typeof payload.access_token !== "string" ||
    typeof payload.expires_in !== "number"
  ) {
    throw new Error("Keycloak token response is missing access_token or expires_in");
  }
  return {
    accessToken: payload.access_token,
    expiresAt: Math.floor(Date.now() / 1000) + payload.expires_in,
  };
}

export function authorizationHeaders(auth) {
  if (Math.floor(Date.now() / 1000) >= auth.expiresAt - 15) {
    Object.assign(auth, mintAccessToken(auth.config));
  }
  return { Authorization: `Bearer ${auth.accessToken}` };
}
