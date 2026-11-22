interface BreadcrumbProps {
  documentTitle?: string;
  path: string[];
}

export default function Breadcrumb({ documentTitle, path }: BreadcrumbProps) {
  return (
    <div className="breadcrumb">
      {documentTitle && <span className="crumb doc">{documentTitle}</span>}
      {path.map((p, i) => (
        <span key={`${i}-${p}`} className={`crumb ${i === path.length - 1 ? "last" : ""}`}>
          {p}
        </span>
      ))}
    </div>
  );
}
