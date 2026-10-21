import type { ReactNode } from "react";
import { Link } from "react-router";
import { Icon, type IconName } from "./Icon";

export type IconTone = "blue" | "green" | "orange" | "red" | "purple" | "gray";

export function IconTile({ name, tone = "blue" }: { name: IconName; tone?: IconTone }) {
  return (
    <span className={`icon-tile ${tone}`}>
      <Icon name={name} size={20} />
    </span>
  );
}

/** An inset grouped list, like iOS settings: rounded section with hairline separators. */
export function ListGroup({ title, label, children }: { title?: string; label?: string; children: ReactNode }) {
  return (
    <section className="group">
      {title && <h2 className="group-title">{title}</h2>}
      <ul className="group-list" aria-label={label ?? title}>
        {children}
      </ul>
    </section>
  );
}

interface ListRowProps {
  title: ReactNode;
  subtitle?: ReactNode;
  icon?: IconName;
  tone?: IconTone;
  leading?: ReactNode;
  trailing?: ReactNode;
  to?: string;
}

/** One list row; with `to` the whole row is a link and gets a chevron. */
export function ListRow({ title, subtitle, icon, tone, leading, trailing, to }: ListRowProps) {
  const body = (
    <>
      {leading ?? (icon && <IconTile name={icon} tone={tone} />)}
      <span className="row-text">
        <span className="row-title">{title}</span>
        {subtitle && <span className="row-subtitle">{subtitle}</span>}
      </span>
      {trailing && <span className="row-trailing">{trailing}</span>}
      {to && (
        <span className="chevron">
          <Icon name="chevron" size={18} />
        </span>
      )}
    </>
  );
  return (
    <li className="list-row">
      {to ? (
        <Link className="row-body" to={to}>
          {body}
        </Link>
      ) : (
        <div className="row-body">{body}</div>
      )}
    </li>
  );
}
