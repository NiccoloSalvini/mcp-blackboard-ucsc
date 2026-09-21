// Letterhead template for the Cattolica note. Quarto passes title, authors,
// date, font, margins, toc through typst-show.typ; the header/footer and the
// subtitle line are fixed here.

#let ucsc-blue = rgb("#193458")
#let ucsc-accent = rgb("#0862A8")
#let ucsc-grey = rgb("#4A4A4A")

#let article(
  title: none,
  authors: none,
  date: none,
  abstract: none,
  abstract-title: none,
  cols: 1,
  margin: (x: 1.25in, y: 1.25in),
  paper: "us-letter",
  lang: "en",
  region: "US",
  font: (),
  fontsize: 11pt,
  sectionnumbering: none,
  toc: false,
  toc_title: none,
  toc_depth: none,
  toc_indent: 1.5em,
  doc,
) = {
  set page(
    paper: paper,
    margin: margin,
    header: [
      #grid(
        columns: (auto, 1fr),
        align: (left + horizon, right + horizon),
        image("assets/logo-unicatt.svg", height: 1.35cm),
        text(size: 8.5pt, fill: ucsc-grey)[
          Facoltà di Economia · Sede di Roma \
          Statistics and Big Data (SSI428)
        ],
      )
      #v(-0.35em)
      #line(length: 100%, stroke: 0.6pt + ucsc-blue)
    ],
    header-ascent: 25%,
    footer: [
      #line(length: 100%, stroke: 0.4pt + luma(180))
      #v(-0.4em)
      #grid(
        columns: (1fr, auto),
        text(size: 8pt, fill: ucsc-grey)[Nota interna · MCP Blackboard per la Cattolica · settembre 2026],
        text(size: 8pt, fill: ucsc-grey)[#counter(page).display("1 / 1", both: true)],
      )
    ],
    footer-descent: 30%,
  )
  set par(justify: true, leading: 0.62em)
  set text(lang: lang, region: region, font: font, size: fontsize, fill: luma(30))
  set heading(numbering: sectionnumbering)
  show heading.where(level: 1): it => block(above: 1.6em, below: 0.7em)[
    #text(fill: ucsc-blue, size: 15pt, weight: 700)[#it]
  ]
  show heading.where(level: 2): it => block(above: 1.2em, below: 0.5em)[
    #text(fill: ucsc-accent, size: 11.5pt, weight: 700)[#it]
  ]
  show link: set text(fill: ucsc-accent)
  set table(inset: 6pt, stroke: (x, y) => if y == 0 { (bottom: 0.7pt + ucsc-blue) } else { (bottom: 0.3pt + luma(200)) })
  show table.cell.where(y: 0): set text(weight: 700, fill: ucsc-blue)
  show figure.caption: set text(size: 9pt, fill: ucsc-grey)

  if title != none {
    v(0.5em)
    block(below: 0.4em)[#text(weight: 700, size: 22pt, fill: ucsc-blue)[#title]]
    block(below: 0.8em)[#text(size: 12pt, fill: ucsc-grey)[
      Cos'è, come lo sto costruendo, come lo userò — e come tratta i dati di studenti e docenti
    ]]
  }

  if authors != none {
    block(below: 0.2em)[
      #text(size: 10.5pt)[
        #authors.map(a => a.name).join(", ") · #authors.map(a => a.affiliation).join(", ")
      ]
    ]
  }
  if date != none {
    block(below: 1.4em)[#text(size: 10pt, fill: ucsc-grey)[Roma, #date]]
  }
  line(length: 100%, stroke: 0.4pt + luma(200))

  if abstract != none {
    block(inset: (x: 0em, y: 1em))[
      #text(weight: 700, fill: ucsc-blue)[#abstract-title] #h(0.6em) #abstract
    ]
  }

  if toc {
    block(above: 1em, below: 1.6em)[
      #text(weight: 700, fill: ucsc-blue)[Indice]
      #v(0.3em)
      #outline(title: none, depth: toc_depth, indent: toc_indent)
    ]
  }

  if cols == 1 { doc } else { columns(cols, doc) }
}
