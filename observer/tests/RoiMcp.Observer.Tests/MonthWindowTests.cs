using RoiMcp.Observer.Core;
using Xunit;

namespace RoiMcp.Observer.Tests
{
    public class MonthWindowTests
    {
        [Fact]
        public void SumsDailyValuesPerMonth()
        {
            var w = new MonthWindow(24);
            w.Reset(96, 1);
            for (int day = 1; day <= 30; day++) w.Add(96, 1, 2.0);
            for (int day = 1; day <= 30; day++) w.Add(95, 12, 1.0);
            Assert.Equal(60.0, w.Value(0, false));
            Assert.Equal(30.0, w.Value(1, false));
            Assert.False(w.Has(2));
        }

        [Fact]
        public void AveragedItemsUseTheMean()
        {
            var w = new MonthWindow(3);
            w.Reset(10, 5);
            w.Add(10, 4, 0.5);
            w.Add(10, 4, 1.0);
            Assert.Equal(0.75, w.Value(1, true), 10);
            Assert.Equal(1.5, w.Value(1, false), 10);
        }

        [Fact]
        public void IgnoresValuesOutsideTheWindowInAnyOrder()
        {
            var w = new MonthWindow(24);
            w.Reset(96, 1);
            w.Add(1, 4, 100);   // first year of the save
            w.Add(94, 1, 7);    // exactly 24 months back: outside
            w.Add(94, 2, 3);    // 23 months back: inside
            w.Add(96, 2, 9);    // future: outside
            w.Add(95, 6, 1);
            Assert.True(w.Has(23));
            Assert.Equal(3, w.Value(23, false));
            Assert.Equal(1, w.Value(7, false));
            int inside = 0;
            for (int k = 0; k < w.Months; k++) if (w.Has(k)) inside++;
            Assert.Equal(2, inside);
        }

        [Fact]
        public void ResetClearsPreviousSeries()
        {
            var w = new MonthWindow(2);
            w.Reset(5, 1);
            w.Add(5, 1, 4);
            w.Reset(5, 1);
            Assert.False(w.Has(0));
            Assert.Equal(0, w.Value(0, false));
        }

        [Theory]
        [InlineData(104, 9, 24, 102, 10, 24)]
        [InlineData(96, 1, 24, 94, 2, 24)]
        [InlineData(96, 1, 12, 95, 2, 12)]
        [InlineData(2, 3, 24, 1, 1, 15)]   // early save: clamped to Y1-01
        [InlineData(1, 1, 24, 1, 1, 1)]
        public void BoundsClampToTheFirstMonth(int year, int month, int months, int fy, int fm, int span)
        {
            int y, m;
            Assert.Equal(span, MonthWindow.Bounds(year, month, months, out y, out m));
            Assert.Equal(fy, y);
            Assert.Equal(fm, m);
        }

        [Theory]
        [InlineData(96, 1, 0, 96, 1)]
        [InlineData(96, 1, 1, 95, 12)]
        [InlineData(96, 1, 23, 94, 2)]
        [InlineData(2, 3, 14, 1, 1)]
        public void BackWalksCalendarMonths(int year, int month, int back, int ey, int em)
        {
            int y, m;
            MonthWindow.Back(year, month, back, out y, out m);
            Assert.Equal(ey, y);
            Assert.Equal(em, m);
        }
    }
}
