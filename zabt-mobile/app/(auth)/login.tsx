// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import { useEffect, useMemo, useState } from "react";
import { Text, View } from "react-native";
import { Link, router } from "expo-router";
import * as AuthSession from "expo-auth-session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  createMicrosoftOidcChallenge,
  getAuthenticationMode,
  getMicrosoftOidcStatus,
  signIn,
  signInWithMicrosoftIdToken,
  type AuthenticationModeResponse,
  type MicrosoftOidcStatus,
} from "@/lib/auth";

const REDIRECT_URI = AuthSession.makeRedirectUri({ scheme: "zabt", path: "auth" });

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authenticationMode, setAuthenticationMode] = useState<AuthenticationModeResponse | null>(null);
  const [microsoftStatus, setMicrosoftStatus] = useState<MicrosoftOidcStatus | null>(null);
  const [modeLoading, setModeLoading] = useState(true);
  const [microsoftStatusLoading, setMicrosoftStatusLoading] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const tenant = microsoftStatus?.tenant ?? "common";
  const clientId = microsoftStatus?.client_id ?? "";
  const discovery = useMemo(() => {
    const authority = `https://login.microsoftonline.com/${encodeURIComponent(tenant)}/oauth2/v2.0`;
    return {
      authorizationEndpoint: `${authority}/authorize`,
      tokenEndpoint: `${authority}/token`,
    };
  }, [tenant]);

  useEffect(() => {
    let cancelled = false;
    getAuthenticationMode()
      .then((mode) => {
        if (!cancelled) setAuthenticationMode(mode);
      })
      .catch(() => {
        if (!cancelled) setAuthenticationMode(null);
      })
      .finally(() => {
        if (!cancelled) setModeLoading(false);
      });
    getMicrosoftOidcStatus()
      .then((status) => {
        if (!cancelled) setMicrosoftStatus(status);
      })
      .catch(() => {
        if (!cancelled) setMicrosoftStatus(null);
      })
      .finally(() => {
        if (!cancelled) setMicrosoftStatusLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSignIn() {
    setLoading(true);
    setError(null);
    try {
      await signIn(email, password);
      router.replace("/(tabs)");
    } catch {
      setError("The email or password is incorrect. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  async function handleMicrosoftSignIn() {
    if (!clientId || !microsoftStatus?.configured) return;
    setLoading(true);
    setError(null);
    try {
      const challenge = await createMicrosoftOidcChallenge();
      const request = new AuthSession.AuthRequest({
        clientId,
        redirectUri: REDIRECT_URI,
        responseType: AuthSession.ResponseType.Code,
        scopes: ["openid", "profile", "email"],
        usePKCE: true,
        extraParams: { nonce: challenge.nonce },
      });
      const result = await request.promptAsync(discovery);
      if (result.type === "cancel" || result.type === "dismiss") {
        setError("Microsoft sign-in was canceled. You can try again when ready.");
        return;
      }
      if (result.type !== "success") {
        setError("Microsoft sign-in could not be completed. Please try again.");
        return;
      }

      const authorizationCode = result.params.code;
      const codeVerifier = request.codeVerifier;
      if (!authorizationCode || !codeVerifier) {
        throw new Error("Microsoft sign-in could not be verified");
      }
      const tokenResponse = await AuthSession.exchangeCodeAsync(
        {
          clientId,
          code: authorizationCode,
          redirectUri: REDIRECT_URI,
          extraParams: { code_verifier: codeVerifier },
        },
        discovery,
      );
      if (!tokenResponse.idToken) {
        throw new Error("Microsoft did not return an ID token");
      }
      await signInWithMicrosoftIdToken(tokenResponse.idToken, challenge.challenge_id);
      router.replace("/(tabs)");
    } catch {
      setError("Microsoft sign-in could not be completed. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  const isLocalMode = authenticationMode?.mode === "local";
  const oidcReady =
    authenticationMode?.mode === "microsoft_oidc" &&
    authenticationMode.oidc_configured &&
    microsoftStatus?.configured === true;

  return (
    <View className="flex-1 justify-center bg-background px-8">
      <View className="mb-10">
        <Text className="mb-1 text-2xl font-bold text-foreground">Welcome to Zabt</Text>
        <Text className="text-sm text-muted-foreground">
          {authenticationMode?.mode === "microsoft_oidc"
            ? "Sign in with your Microsoft work account."
            : "Sign in with your Zabt account."}
        </Text>
      </View>

      {modeLoading ? (
        <Text className="text-sm text-muted-foreground" accessibilityRole="text">
          Loading sign-in options…
        </Text>
      ) : !authenticationMode ? (
        <Text className="text-sm text-muted-foreground" accessibilityRole="alert">
          Sign-in options are temporarily unavailable. Please try again later.
        </Text>
      ) : isLocalMode ? (
        <View className="gap-4">
          <View className="gap-2">
            <Text className="text-sm font-medium text-foreground">Email</Text>
            <Input
              autoCapitalize="none"
              autoComplete="email"
              keyboardType="email-address"
              value={email}
              onChangeText={setEmail}
              placeholder="name@company.com"
              accessibilityLabel="Email"
            />
          </View>
          <View className="gap-2">
            <Text className="text-sm font-medium text-foreground">Password</Text>
            <Input
              secureTextEntry
              autoComplete="password"
              value={password}
              onChangeText={setPassword}
              placeholder="Your password"
              accessibilityLabel="Password"
            />
          </View>
          {error && <Text className="text-sm text-destructive" accessibilityRole="alert">{error}</Text>}
          <Button
            onPress={handleSignIn}
            loading={loading}
            disabled={!email.trim() || !password}
            className="mt-2"
          >
            Sign in
          </Button>
        </View>
      ) : (
        <View className="gap-4">
          {microsoftStatusLoading ? (
            <Text className="text-sm text-muted-foreground" accessibilityRole="text">
              Checking Microsoft sign-in…
            </Text>
          ) : oidcReady ? (
            <Button
              onPress={handleMicrosoftSignIn}
              loading={loading}
              disabled={!clientId || loading}
              className="mt-2"
            >
              Continue with Microsoft
            </Button>
          ) : (
            <Text className="text-sm text-muted-foreground" accessibilityRole="alert">
              Microsoft sign-in is selected for this instance but is not available right now. Local sign-in remains disabled; contact the system administrator.
            </Text>
          )}
          {error && <Text className="text-sm text-destructive" accessibilityRole="alert">{error}</Text>}
        </View>
      )}

      {!modeLoading && isLocalMode && (
        <Text className="mt-6 text-center text-sm text-muted-foreground">
          New to Zabt?{" "}
          <Link href="/(auth)/register" className="font-medium text-primary">
            Create an account
          </Link>
        </Text>
      )}
      <Text className="mt-4 text-center text-xs text-muted-foreground">
        Password reset and email verification are not configured yet.
      </Text>
    </View>
  );
}
