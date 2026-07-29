import { useRef, useState } from "react";
import { fetchAdminOverview } from "../api/admin";
import { ForbiddenError, UnauthorizedError } from "../api/client";
import type { AdminOverview } from "../workspace/types";

type UseAdminOverviewOptions = {
  onUnauthorized: () => void;
};

export function useAdminOverview({ onUnauthorized }: UseAdminOverviewOptions) {
  const [adminOverview, setAdminOverview] = useState<AdminOverview | null>(null);
  const [adminError, setAdminError] = useState<string | null>(null);
  const [isAdminLoading, setIsAdminLoading] = useState(false);
  const adminOverviewRequestRef = useRef<Promise<void> | null>(null);

  const clearAdminOverview = () => {
    setAdminOverview(null);
    setAdminError(null);
    setIsAdminLoading(false);
  };

  const loadAdminOverview = (signal?: AbortSignal): Promise<void> => {
    if (adminOverviewRequestRef.current) {
      return adminOverviewRequestRef.current;
    }

    const request = (async () => {
      setIsAdminLoading(true);
      setAdminError(null);
      try {
        setAdminOverview(await fetchAdminOverview(signal));
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        if (error instanceof UnauthorizedError) {
          onUnauthorized();
          setAdminOverview(null);
          return;
        }
        if (error instanceof ForbiddenError) {
          setAdminOverview(null);
          setAdminError(error.message);
          return;
        }
        setAdminError(
          error instanceof Error ? error.message : "Admin overview request failed",
        );
      } finally {
        if (!signal?.aborted) {
          setIsAdminLoading(false);
        }
      }
    })();
    adminOverviewRequestRef.current = request;
    const clearRequest = () => {
      if (adminOverviewRequestRef.current === request) {
        adminOverviewRequestRef.current = null;
      }
    };
    const handleSettled = () => {
      signal?.removeEventListener("abort", clearRequest);
      clearRequest();
    };
    if (signal?.aborted) {
      clearRequest();
    } else {
      signal?.addEventListener("abort", clearRequest, { once: true });
    }
    void request.then(handleSettled, handleSettled);
    return request;
  };

  return {
    adminError,
    adminOverview,
    clearAdminOverview,
    isAdminLoading,
    loadAdminOverview,
  };
}
