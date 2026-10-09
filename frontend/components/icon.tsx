const paths = {
  file: "M14 2H5v20h14V7l-5-5Zm0 0v6h5M8 12h8M8 16h6",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  check: "m5 12 4 4L19 6",
  cap: "m2 9 10-5 10 5-10 5L2 9Zm4 3v5l6 3 6-3v-5M22 9v7",
  folder: "M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7Z",
  chat: "M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2 1.7-5.5A8.5 8.5 0 1 1 21 11.5Z",
  book: "M12 5v15M12 5C8 2 3 4 3 4v15s5-2 9 1c4-3 9-1 9-1V4s-5-2-9 1Z",
  user: "M20 21v-2a7 7 0 0 0-14 0v2M17 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z",
  arrow: "M4 12h16m-6-6 6 6-6 6", chevron: "m9 5 7 7-7 7",
  down: "m6 9 6 6 6-6", plus: "M12 5v14M5 12h14",
  close: "m6 6 12 12M6 18 18 6", menu: "M4 6h16M4 12h16M4 18h16",
};
export default function Icon({ name, size = 22 }: { name: keyof typeof paths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
