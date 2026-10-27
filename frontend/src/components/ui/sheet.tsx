"use client";

// A left/right sliding panel built on the same Radix Dialog the app already
// uses for modals (#557). The sidebar was `hidden lg:flex`, so below `lg`
// there was no way to navigate at all; this is the surface the hamburger
// opens. Radix gives the focus trap, Escape handling, aria-modal and the
// backdrop click-to-close for free — the same guarantees `DialogContent`
// relies on.
import * as React from "react";
import * as SheetPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const Sheet = SheetPrimitive.Root;
const SheetTrigger = SheetPrimitive.Trigger;
const SheetClose = SheetPrimitive.Close;

/** Required for the same reason `DialogTitle` is: a dialog with no accessible
 * name is an axe violation. The mobile nav's title is visually hidden. */
const SheetTitle = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Title>
>(({ className, ...props }, ref) => (
  <SheetPrimitive.Title ref={ref} className={cn(className)} {...props} />
));
SheetTitle.displayName = "SheetTitle";

const SheetOverlay = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <SheetPrimitive.Overlay
    ref={ref}
    className={cn("fixed inset-0 z-50 bg-black/60 backdrop-blur-sm", className)}
    {...props}
  />
));
SheetOverlay.displayName = "SheetOverlay";

const SIDE_CLASSES: Record<"left" | "right", string> = {
  left: "inset-y-0 left-0 h-full w-[280px] border-r",
  right: "inset-y-0 right-0 h-full w-[280px] border-l",
};

const SheetContent = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Content> & { side?: "left" | "right" }
>(({ className, children, side = "left", ...props }, ref) => {
  const t = useT();
  return (
    <SheetPrimitive.Portal>
      <SheetOverlay />
      <SheetPrimitive.Content
        ref={ref}
        className={cn(
          "fixed z-50 flex flex-col bg-background shadow-xl transition ease-in-out",
          SIDE_CLASSES[side],
          className
        )}
        {...props}
      >
        {children}
        <SheetPrimitive.Close
          aria-label={t("ui.sheet.close")}
          className="absolute right-3 top-4 rounded-sm opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring/30"
        >
          <X className="h-4 w-4" />
        </SheetPrimitive.Close>
      </SheetPrimitive.Content>
    </SheetPrimitive.Portal>
  );
});
SheetContent.displayName = "SheetContent";

export { Sheet, SheetTrigger, SheetClose, SheetContent, SheetTitle };
