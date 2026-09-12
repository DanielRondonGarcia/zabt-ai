// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import { useState } from "react";
import { Alert, Text, View } from "react-native";
import { Link, router } from "expo-router";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { register } from "@/lib/auth";

export default function Register() {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleRegister() {
    setLoading(true);
    try {
      await register(email, password, fullName);
      router.replace("/(tabs)");
    } catch {
      Alert.alert(
        "Registration failed",
        "The email may already be registered, or the password may be too short."
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <View className="flex-1 bg-background px-8 justify-center">
      <View className="mb-8">
        <Text className="text-2xl font-bold text-foreground mb-1">
          Create your account
        </Text>
        <Text className="text-sm text-muted-foreground">
          Your credentials stay with this Zabt installation.
        </Text>
      </View>

      <View className="gap-4">
        <View className="gap-2">
          <Text className="text-sm font-medium text-foreground">Full name</Text>
          <Input
            autoComplete="name"
            value={fullName}
            onChangeText={setFullName}
            placeholder="Your name"
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
          />
        </View>
        <Button
          onPress={handleRegister}
          loading={loading}
          disabled={!email.trim() || password.length < 8}
          className="mt-2"
        >
          Create account
        </Button>
      </View>

      <Text className="text-sm text-muted-foreground text-center mt-6">
        Already have an account?{" "}
        <Link href="/(auth)/login" className="text-primary font-medium">
          Sign in
        </Link>
      </Text>
      <Text className="text-xs text-muted-foreground text-center mt-4">
        Email verification and password reset are not configured yet.
      </Text>
    </View>
  );
}
