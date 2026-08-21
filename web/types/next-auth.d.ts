import "next-auth";

declare module "next-auth" {
  interface Session {
    accessToken?: string;
    accessTokenExpiresAt?: number;
    authorizedParty?: string;
    error?: "RefreshAccessTokenError";
    idToken?: string;
    refreshToken?: string;
    roles: string[];
  }
}
