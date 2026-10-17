"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  getApiErrorMessage,
  getApiErrorStatus,
  getStorageConfiguration,
  listStorageObjects,
  updateStorageConfiguration,
  type StorageConfigurationRead,
  type StorageConfigurationUpdate,
  type StorageObjectEntry,
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
  const [endpointUrl, setEndpointUrl] = useState("");
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
      setEndpointUrl(saved.s3_endpoint_url ?? "");
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
          "Enter the region, bucket, access key ID, and secret access key before saving S3-compatible storage settings.",
      });
      return;
    }

    const payload: StorageConfigurationUpdate = {
      provider,
      s3_endpoint_url: endpointUrl.trim() || null,
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
      setEndpointUrl(saved.s3_endpoint_url ?? "");
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
          "Storage settings could not be saved. Check the S3-compatible storage values and try again.",
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
            {configuration.provider === "aws_s3" ? "S3-compatible" : "MinIO"}
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
                <SelectItem value="aws_s3">S3-compatible storage</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {provider === "aws_s3" && (
            <div className="space-y-4">
              <div>
                <label
                  htmlFor="global-storage-endpoint"
                  className="mb-1 block text-sm font-medium text-foreground"
                >
                  S3 endpoint URL (optional)
                </label>
                <Input
                  id="global-storage-endpoint"
                  type="url"
                  value={endpointUrl}
                  onChange={(event) => setEndpointUrl(event.target.value)}
                  placeholder="https://<namespace>.compat.objectstorage.<region>.oraclecloud.com"
                  autoComplete="url"
                  disabled={saving}
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  Leave blank for AWS S3. For Oracle OCI or another S3-compatible service, enter its full HTTPS endpoint; configure the bucket separately.
                </p>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="global-storage-region"
                    className="mb-1 block text-sm font-medium text-foreground"
                  >
                    Region
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

      {configuration?.provider === "aws_s3" && (
        <div className="mt-5 border-t border-border pt-5">
          <S3ObjectExplorer
            key={`${configuration.s3_endpoint_url ?? ""}:${configuration.aws_bucket ?? ""}:${configuration.aws_access_key_id ?? ""}`}
            bucket={configuration.aws_bucket ?? ""}
          />
        </div>
      )}
    </section>
  );
}

function S3ObjectExplorer({ bucket }: { bucket: string }) {
  const [isOpen, setIsOpen] = useState(false);
  const [prefix, setPrefix] = useState("");
  const [objects, setObjects] = useState<StorageObjectEntry[]>([]);
  const [prefixes, setPrefixes] = useState<string[]>([]);
  const [nextToken, setNextToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPage = useCallback(
    async (targetPrefix: string, token: string | null, append: boolean) => {
      setLoading(true);
      setError(null);
      if (!append) {
        setObjects([]);
        setPrefixes([]);
        setNextToken(null);
      }
      try {
        const page = await listStorageObjects(targetPrefix, token);
        setObjects((current) => (append ? [...current, ...page.objects] : page.objects));
        setPrefixes((current) =>
          append ? Array.from(new Set([...current, ...page.prefixes])) : page.prefixes,
        );
        setNextToken(page.next_continuation_token);
        setPrefix(targetPrefix);
      } catch (loadError) {
        setError(
          getApiErrorMessage(loadError) ??
            "Could not list the configured S3 bucket. Check its endpoint and ListBucket permission.",
        );
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  const toggleExplorer = () => {
    if (isOpen) {
      setIsOpen(false);
      return;
    }
    setIsOpen(true);
    void loadPage("", null, false);
  };

  const goToParent = () => {
    const withoutTrailingSlash = prefix.endsWith("/") ? prefix.slice(0, -1) : prefix;
    const lastSlash = withoutTrailingSlash.lastIndexOf("/");
    const parent = lastSlash < 0 ? "" : withoutTrailingSlash.slice(0, lastSlash + 1);
    void loadPage(parent, null, false);
  };

  const formatSize = (size: number) => {
    if (size < 1024) return `${size} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let value = size / 1024;
    let unitIndex = 0;
    while (value >= 1024 && unitIndex < units.length - 1) {
      value /= 1024;
      unitIndex += 1;
    }
    return `${value.toFixed(1)} ${units[unitIndex]}`;
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-foreground">S3 bucket explorer</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            Read-only browsing requires S3 ListBucket permission. This does not test uploads or processing.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={toggleExplorer}
          disabled={loading}
        >
          {isOpen ? "Close explorer" : "Browse bucket"}
        </Button>
      </div>

      {isOpen && (
        <div className="space-y-3" aria-busy={loading}>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-muted-foreground">Bucket</span>
            <code className="rounded-md bg-muted px-2 py-1 text-foreground">{bucket || "—"}</code>
            <span className="text-muted-foreground">Prefix</span>
            <code className="min-w-0 break-all rounded-md bg-muted px-2 py-1 text-foreground">
              {prefix || "/"}
            </code>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={goToParent}
              disabled={loading || !prefix}
            >
              Up one level
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void loadPage(prefix, null, false)}
              disabled={loading}
            >
              Refresh
            </Button>
          </div>

          {loading && (
            <p className="text-sm text-muted-foreground" role="status" aria-live="polite">
              Loading bucket contents…
            </p>
          )}
          {error && <p className="text-sm text-destructive" role="alert">{error}</p>}

          {!loading && !error && prefixes.length === 0 && objects.length === 0 && (
            <p className="text-sm text-muted-foreground" role="status">
              This prefix is empty.
            </p>
          )}

          {(prefixes.length > 0 || objects.length > 0) && (
            <div className="overflow-hidden rounded-lg border border-border">
              <div className="grid grid-cols-[minmax(0,1fr)_5rem] gap-3 border-b border-border bg-muted/50 px-3 py-2 text-sm font-medium text-muted-foreground sm:grid-cols-[minmax(0,1fr)_6rem_12rem]">
                <span>Name</span>
                <span>Size</span>
                <span className="hidden sm:block">Last modified</span>
              </div>
              {prefixes.map((folderPrefix) => (
                <div
                  key={`prefix:${folderPrefix}`}
                  className="grid grid-cols-[minmax(0,1fr)_5rem] items-center gap-3 border-b border-border px-3 py-2 last:border-b-0 sm:grid-cols-[minmax(0,1fr)_6rem_12rem]"
                >
                  <button
                    type="button"
                    className="min-w-0 break-all text-left text-sm text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    onClick={() => void loadPage(folderPrefix, null, false)}
                    disabled={loading}
                    aria-label={`Open folder ${folderPrefix}`}
                  >
                    {folderPrefix.slice(prefix.length).replace(/\/$/, "") || folderPrefix}
                  </button>
                  <span className="text-sm text-muted-foreground">Folder</span>
                  <span className="hidden text-sm text-muted-foreground sm:block">—</span>
                </div>
              ))}
              {objects.map((object) => (
                <div
                  key={object.key}
                  className="grid grid-cols-[minmax(0,1fr)_5rem] items-center gap-3 border-b border-border px-3 py-2 last:border-b-0 sm:grid-cols-[minmax(0,1fr)_6rem_12rem]"
                >
                  <code className="min-w-0 break-all text-sm text-foreground">
                    {object.key.slice(prefix.length)}
                  </code>
                  <span className="text-sm text-muted-foreground">{formatSize(object.size)}</span>
                  <span className="hidden text-sm text-muted-foreground sm:block">
                    {object.last_modified ? new Date(object.last_modified).toLocaleString() : "—"}
                  </span>
                </div>
              ))}
            </div>
          )}

          {nextToken && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              loading={loading}
              disabled={loading}
              onClick={() => void loadPage(prefix, nextToken, true)}
            >
              Load more
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
