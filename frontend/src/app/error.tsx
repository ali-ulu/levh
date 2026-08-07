"use client";

import { useEffect } from "react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="premium-card m-6 rounded-[22px] border p-6">
      <h2 className="text-base font-semibold">Something went wrong</h2>
      <p className="mt-2 whitespace-pre-wrap text-sm text-muted-foreground">
        {error.message || "An unexpected client-side error occurred."}
      </p>
      <button
        onClick={reset}
        className="mini-action mt-4"
      >
        Try again
      </button>
    </div>
  );
}
