// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import { useEffect, useState } from "react";
import { Text, View } from "react-native";
import { Link, router } from "expo-router";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getAuthenticationMode, register, type AuthenticationModeResponse } from "@/lib/auth";

export default function Register() {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [modeLoading, setModeLoading] = useState(true);
  const [authenticationMode, setAuthenticationMode] = useState<AuthenticationModeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleRegister() {
    if (authenticationMode?.mode !== "local") return;
    setLoading(true);
    setError(null);
    try {
      await register(email, password, fullName);
      router.replace("/(tabs)");
    } catch {
      setError("Registration could not be completed. Check the email and password, then try again.");
    } finally {
      setLoading(false);
    }
  }

  const isLocalMode = authenticationMode?.mode === "local";

  return (
    <View className="flex-1 justify-center bg-background px-8">
      <View className="mb-8">
        <Text className="mb-1 text-2xl font-bold text-foreground">Create your account</Text>
        <Text className="text-sm text-muted-foreground">
          {authenticationMode?.mode === "microsoft_oidc"
            ? "This instance uses Microsoft sign-in for new accounts."
            : "Your credentials stay with this Zabt installation."}
        </Text>
      </View>

      {modeLoading ? (
        <Text className="text-sm text-muted-foreground" accessibilityRole="text">
          Checking registration options…
        </Text>
      ) : !authenticationMode ? (
        <Text className="text-sm text-muted-foreground" accessibilityRole="alert">
          Registration options are temporarily unavailable. Return to sign-in or try again later.
        </Text>
      ) : isLocalMode ? (
        <View className="gap-4">
          <View className="gap-2">
            <Text className="text-sm font-medium text-foreground">Full name</Text>
            <Input
              autoComplete="name"
              value={fullName}
              onChangeText={setFullName}
              placeholder="Your name"
              accessibilityLabel="Full name"
            />
          </View>
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
              autoComplete="new-password"
              value={password}
              onChangeText={setPassword}
              placeholder="At least 8 characters"
              accessibilityLabel="Password"
            />
          </View>
          {error && <Text className="text-sm text-destructive" accessibilityRole="alert">{error}</Text>}
          <Button
            onPress={handleRegister}
            loading={loading}
            disabled={!email.trim() || password.length < 8}
            className="mt-2"
          >
            Create account
          </Button>
        </View>
      ) : (
        <Text className="text-sm text-muted-foreground" accessibilityRole="alert">
          Local registration is disabled because this instance uses Microsoft sign-in. Continue from the sign-in page.
        </Text>
      )}

      <Text className="mt-6 text-center text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link href="/(auth)/login" className="font-medium text-primary">
          Sign in
        </Link>
      </Text>
      <Text className="mt-4 text-center text-xs text-muted-foreground">
        Email verification and password reset are not configured yet.
      </Text>
    </View>
  );
}
