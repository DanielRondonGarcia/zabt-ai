// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import { useState } from "react";
import { Alert, Text, View } from "react-native";
import { Link, router } from "expo-router";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { signIn } from "@/lib/auth";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSignIn() {
    setLoading(true);
    try {
      await signIn(email, password);
      router.replace("/(tabs)");
    } catch {
      Alert.alert(
        "Sign in failed",
        "The email or password is incorrect. Please try again."
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <View className="flex-1 bg-background px-8 justify-center">
      <View className="mb-10">
        <Text className="text-2xl font-bold text-foreground mb-1">
          Welcome to Zabt
        </Text>
        <Text className="text-sm text-muted-foreground">
          Sign in with your local Zabt account.
        </Text>
      </View>

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
          />
        </View>
        <Button
          onPress={handleSignIn}
          loading={loading}
          disabled={!email.trim() || !password}
          className="mt-2"
        >
          Sign in
        </Button>
      </View>

      <Text className="text-sm text-muted-foreground text-center mt-6">
        New to Zabt?{" "}
        <Link href="/(auth)/register" className="text-primary font-medium">
          Create an account
        </Link>
      </Text>
      <Text className="text-xs text-muted-foreground text-center mt-4">
        Password reset and email verification are not configured yet.
      </Text>
    </View>
  );
}
