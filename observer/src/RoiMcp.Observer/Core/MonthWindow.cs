using System;

namespace RoiMcp.Observer.Core
{
    /// <summary>
    /// Aggregates dated values into the last N calendar months (index 0 = current month), combining one month's
    /// values the way BuildingAnalysis.AccumulateValue does: sum, or mean for averaged items. Input order does not
    /// matter; values outside the window are ignored. Reusable scratch, no allocation per value.
    /// </summary>
    public sealed class MonthWindow
    {
        private readonly double[] _sums;
        private readonly int[] _counts;
        private int _nowKey;

        public MonthWindow(int months)
        {
            if (months < 1) throw new ArgumentOutOfRangeException("months");
            _sums = new double[months];
            _counts = new int[months];
        }

        public int Months { get { return _sums.Length; } }

        public void Reset(int year, int month)
        {
            Array.Clear(_sums, 0, _sums.Length);
            Array.Clear(_counts, 0, _counts.Length);
            _nowKey = year * 12 + (month - 1);
        }

        public void Add(int year, int month, double value)
        {
            int back = _nowKey - (year * 12 + (month - 1));
            if (back < 0 || back >= _sums.Length) return;
            _sums[back] += value;
            _counts[back]++;
        }

        public bool Has(int monthsBack)
        {
            return _counts[monthsBack] > 0;
        }

        public double Value(int monthsBack, bool averaged)
        {
            int n = _counts[monthsBack];
            if (n == 0) return 0;
            return averaged ? _sums[monthsBack] / n : _sums[monthsBack];
        }

        /// <summary>
        /// First month of a window of <paramref name="months"/> calendar months ending with (year, month), clamped to
        /// Y1-01. Returns the number of months the window actually spans.
        /// </summary>
        public static int Bounds(int year, int month, int months, out int firstYear, out int firstMonth)
        {
            Back(year, month, months - 1, out firstYear, out firstMonth);
            if (firstYear < 1)
            {
                firstYear = 1;
                firstMonth = 1;
            }
            return (year * 12 + month) - (firstYear * 12 + firstMonth) + 1;
        }

        /// <summary>Calendar month that lies monthsBack months before (year, month).</summary>
        public static void Back(int year, int month, int monthsBack, out int y, out int m)
        {
            int key = year * 12 + (month - 1) - monthsBack;
            y = key >= 0 ? key / 12 : (key - 11) / 12;
            m = key - y * 12 + 1;
        }
    }
}
