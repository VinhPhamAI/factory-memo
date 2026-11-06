import { cn } from "@/utils/cn";

/** Render the Factory Memo memo mark as a decorative image. */
export function BrandMark({ small = false }: { small?: boolean }) {
  return (
    <img
      src="/factory-memo.svg"
      alt=""
      aria-hidden="true"
      className={cn("brand-mark", small && "brand-mark-small")}
    />
  );
}
