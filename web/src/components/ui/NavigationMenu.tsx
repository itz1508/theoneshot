/**
 * NavigationMenu — Radix-based navigation menu primitives.
 * Composable API mirroring shadcn/ui:
 *
 *   <NavigationMenu>
 *     <NavigationMenuList>
 *       <NavigationMenuItem>
 *         <NavigationMenuTrigger>Item One</NavigationMenuTrigger>
 *         <NavigationMenuContent>
 *           <NavigationMenuLink>Link</NavigationMenuLink>
 *         </NavigationMenuContent>
 *       </NavigationMenuItem>
 *     </NavigationMenuList>
 *   </NavigationMenu>
 */

import type { ComponentPropsWithoutRef } from 'react'
import * as NavigationMenuPrimitive from '@radix-ui/react-navigation-menu'
import { ChevronDown } from 'lucide-react'
import styles from './NavigationMenu.module.css'

function cx(...parts: Array<string | undefined>) {
  return parts.filter(Boolean).join(' ')
}

/* ─── NavigationMenu (Root) ─── */
export function NavigationMenu({
  children,
  className,
  ...props
}: ComponentPropsWithoutRef<typeof NavigationMenuPrimitive.Root>) {
  return (
    <NavigationMenuPrimitive.Root className={cx(styles.root, className)} {...props}>
      {children}
      <div className={styles.viewportPosition}>
        <NavigationMenuPrimitive.Viewport className={styles.viewport} />
      </div>
    </NavigationMenuPrimitive.Root>
  )
}

/* ─── NavigationMenuList ─── */
export function NavigationMenuList({
  className,
  ...props
}: ComponentPropsWithoutRef<typeof NavigationMenuPrimitive.List>) {
  return <NavigationMenuPrimitive.List className={cx(styles.list, className)} {...props} />
}

/* ─── NavigationMenuItem ─── */
export const NavigationMenuItem = NavigationMenuPrimitive.Item

/* ─── NavigationMenuTrigger ─── */
export function NavigationMenuTrigger({
  children,
  className,
  ...props
}: ComponentPropsWithoutRef<typeof NavigationMenuPrimitive.Trigger>) {
  return (
    <NavigationMenuPrimitive.Trigger className={cx(styles.trigger, className)} {...props}>
      {children}
      <ChevronDown size={12} className={styles.chevron} aria-hidden />
    </NavigationMenuPrimitive.Trigger>
  )
}

/* ─── NavigationMenuContent ─── */
export function NavigationMenuContent({
  className,
  ...props
}: ComponentPropsWithoutRef<typeof NavigationMenuPrimitive.Content>) {
  return <NavigationMenuPrimitive.Content className={cx(styles.content, className)} {...props} />
}

/* ─── NavigationMenuLink ─── */
export function NavigationMenuLink({
  className,
  ...props
}: ComponentPropsWithoutRef<typeof NavigationMenuPrimitive.Link>) {
  return <NavigationMenuPrimitive.Link className={cx(styles.link, className)} {...props} />
}

/* ─── NavigationMenuIndicator ─── */
export function NavigationMenuIndicator({
  className,
  ...props
}: ComponentPropsWithoutRef<typeof NavigationMenuPrimitive.Indicator>) {
  return (
    <NavigationMenuPrimitive.Indicator className={cx(styles.indicator, className)} {...props}>
      <div className={styles.arrow} />
    </NavigationMenuPrimitive.Indicator>
  )
}
