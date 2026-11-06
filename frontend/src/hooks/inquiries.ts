import { useMemo } from "react";
import { useAuthClient } from "@/hooks/auth";
import { InquiriesApi } from "@/services/inquiries-api";

/** One inquiries client per signed-in tab session. */
export function useInquiriesApi(): InquiriesApi {
  const auth = useAuthClient();
  return useMemo(() => new InquiriesApi(auth), [auth]);
}
