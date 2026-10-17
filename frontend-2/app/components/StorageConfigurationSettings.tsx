"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  getApiErrorMessage,
  getApiErrorStatus,
  getStorageConfiguration,
  updateStorageConfiguration,
  type StorageConfigurationRead,
  type StorageConfigurationUpdate,
  type StorageProvider,
} from "@/app/lib/api";
import { Badge } from "@/app/components/ui/badge";
import { Button } from "@/app/components/ui/button";
import { Input } from "@/app/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/app/components/ui/select";

type Feedback = {
  kind: "success" | "error";
  message: string;
};

export function StorageConfigurationSettings() {
  const [configuration, setConfiguration] = useState<StorageConfigurationRead | null>(null);
  const [provider, setProvider] = useState<StorageProvider>("minio");
  const [region, setRegion] = useState("");
  const [bucket, setBucket] = useState("");
  const [accessKeyId, setAccessKeyId] = useState("");
  const [secretAccessKey, setSecretAccessKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [accessDenied, setAccessDenied] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const loadConfiguration = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const saved = await getStorageConfiguration();
      setConfiguration(saved);
      setProvider(saved.provider);
      setRegion(saved.aws_region ?? "");
      setBucket(saved.aws_bucket ?? "");
      setAccessKeyId(saved.aws_access_key_id ?? "");
      setSecretAccessKey("");
    } catch (error) {
      if (getApiErrorStatus(error) === 403) {
        setAccessDenied(true);
      } else {
        setLoadError(true);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConfiguration();
  }, [loadConfiguration]);

  const handleSave = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFeedback(null);

    if (
      provider === "aws_s3" &&
      (!region.trim() ||
        !bucket.trim() ||
        !accessKeyId.trim() ||
        (!configuration?.aws_secret_access_key_configured && !secretAccessKey.trim()))
    ) {
      setFeedback({
        kind: "error",
        message:
          "Enter the AWS region, bucket, access key ID, and secret access key before saving AWS S3 settings.",
      });
      return;
    }

    const payload: StorageConfigurationUpdate = {
      provider,
      aws_region: region.trim() || null,
      aws_bucket: bucket.trim() || null,
      aws_access_key_id: accessKeyId.trim() || null,
      ...(secretAccessKey.trim()
        ? { aws_secret_access_key: secretAccessKey.trim() }
        : {}),
    };

    setSaving(true);
    try {
      const saved = await updateStorageConfiguration(payload);
      setConfiguration(saved);
      setProvider(saved.provider);
      setRegion(saved.aws_region ?? "");
      setBucket(saved.aws_bucket ?? "");
      setAccessKeyId(saved.aws_access_key_id ?? "");
      setSecretAccessKey("");
      setFeedback({
        kind: "success",
        message: "Storage settings saved. Existing meetings will continue using their recorded provider.",
      });
    } catch (error) {
      if (getApiErrorStatus(error) === 403) {
        setAccessDenied(true);
        return;
      }
      setFeedback({
        kind: "error",
        message:
          getApiErrorMessage(error) ??
          "Storage settings could not be saved. Check the AWS values and try again.",
      });
    } finally {
      setSaving(false);
    }
  };

  if (accessDenied) return null;

  return (
    <section className="mb-10 rounded-lg border border-border bg-muted/30 p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-foreground">Global storage</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Choose the provider for new meeting uploads. Existing meetings keep using their
            recorded provider; changing this setting does not move files.
          </p>
        </div>
        {configuration && (
          <Badge variant="outline">
            {configuration.provider === "aws_s3" ? "AWS S3" : "MinIO"}
          </Badge>
        )}
      </div>

      {loading ? (
        <p className="mt-4 text-sm text-muted-foreground" role="status">
          Loading storage settings…
        </p>
      ) : loadError ? (
        <div className="mt-4 flex flex-wrap items-center gap-3" role="alert">
          <p className="text-sm text-destructive">
            Global storage settings could not be loaded.
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => void loadConfiguration()}
          >
            Try again
          </Button>
        </div>
      ) : configuration ? (
        <form onSubmit={handleSave} className="mt-5 space-y-4 border-t border-border pt-5">
          <div>
            <label
              htmlFor="global-storage-provider"
              className="mb-1 block text-sm font-medium text-foreground"
            >
              Provider
            </label>
            <Select
              value={provider}
              onValueChange={(value) => {
                if (value === "minio" || value === "aws_s3") {
                  setProvider(value);
                  setFeedback(null);
                }
              }}
              disabled={saving}
              name="global-storage-provider"
            >
              <SelectTrigger id="global-storage-provider">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="minio">MinIO</SelectItem>
                <SelectItem value="aws_s3">AWS S3</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {provider === "aws_s3" && (
            <div className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="global-storage-region"
                    className="mb-1 block text-sm font-medium text-foreground"
                  >
                    AWS region
                  </label>
                  <Input
                    id="global-storage-region"
                    value={region}
                    onChange={(event) => setRegion(event.target.value)}
                    autoComplete="off"
                    disabled={saving}
                    required
                  />
                </div>
                <div>
                  <label
                    htmlFor="global-storage-bucket"
                    className="mb-1 block text-sm font-medium text-foreground"
                  >
                    Bucket
                  </label>
                  <Input
                    id="global-storage-bucket"
                    value={bucket}
                    onChange={(event) => setBucket(event.target.value)}
                    autoComplete="off"
                    disabled={saving}
                    required
                  />
                </div>
                <div>
                  <label
                    htmlFor="global-storage-access-key"
                    className="mb-1 block text-sm font-medium text-foreground"
                  >
                    Access key ID
                  </label>
                  <Input
                    id="global-storage-access-key"
                    value={accessKeyId}
                    onChange={(event) => setAccessKeyId(event.target.value)}
                    autoComplete="off"
                    disabled={saving}
                    required
                  />
                </div>
                <div>
                  <label
                    htmlFor="global-storage-secret-key"
                    className="mb-1 block text-sm font-medium text-foreground"
                  >
                    Secret access key
                  </label>
                  <Input
                    id="global-storage-secret-key"
                    type="password"
                    value={secretAccessKey}
                    onChange={(event) => setSecretAccessKey(event.target.value)}
                    autoComplete="new-password"
                    disabled={saving}
                    required={!configuration.aws_secret_access_key_configured}
                  />
                  <p className="mt-1 text-xs text-muted-foreground">
                    Leave blank to keep the current secret. The saved secret is encrypted and never displayed.
                  </p>
                </div>
              </div>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" loading={saving} disabled={saving || loading}>
              Save storage settings
            </Button>
            {feedback && (
              <p
                className={
                  feedback.kind === "success"
                    ? "text-sm text-foreground"
                    : "text-sm text-destructive"
                }
                role={feedback.kind === "error" ? "alert" : "status"}
                aria-live="polite"
              >
                {feedback.message}
              </p>
            )}
          </div>
        </form>
      ) : null}
    </section>
  );
}
